"""A real database and real NATS servers.

The database needs no container: SQLite is a file, so each test session gets a
migrated temporary one. Only NATS needs Docker.

Two NATS servers, deliberately:

  nats_full  JetStream + http_port + $SYS   -- the connected-with-everything path
  nats_bare  JetStream only                 -- no monitoring, no system account

`nats_bare` is the important one. It is what turns "we show the fix instead of a
zero" from a design promise into a test that fails when it stops being true.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NATS_IMAGE = "nats:2.14-alpine"


@pytest.fixture(scope="session")
def docker_engine() -> None:
    """A reachable Docker daemon, or a clear answer about why not.

    Skipping is the friendly answer on a laptop without Docker. In CI it is not:
    a skipped integration suite is a green build that tested nothing, which is
    exactly the kind of quiet lie this suite exists to catch. So there it fails.
    """
    try:
        import docker

        docker.from_env().ping()
    except Exception as exc:
        if os.environ.get("CI"):
            pytest.fail(f"Docker is required for the integration suite in CI: {exc}", pytrace=False)
        pytest.skip(f"Docker is not available for integration tests: {exc}")


@pytest.fixture(scope="session")
def nats_image(docker_engine: None) -> str:
    """The image every container in this suite runs, and a Docker check with it.

    A fixture rather than an imported constant so the version is bumped in one
    place without a test module having to import its own conftest by name.
    """
    return NATS_IMAGE


@pytest.fixture(scope="session")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A migrated SQLite database in a temp directory.

    No container and no skip: the database half of the integration suite runs
    anywhere, which is most of the reason to prefer SQLite for the registry.
    """
    if url := os.environ.get("TEST_DATABASE_URL"):
        yield url
        return

    from alembic import command
    from alembic.config import Config

    path = tmp_path_factory.mktemp("registry") / "nats-lens.db"
    url = f"sqlite+aiosqlite:///{path}"

    cfg = Config(str(REPO / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO / "backend/nats_lens/db/migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    os.environ["DATABASE_URL"] = url
    command.upgrade(cfg, "head")

    yield url


def _nats_container(config: str) -> Iterator[str]:
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy

    container = (
        DockerContainer(NATS_IMAGE)
        .with_command("-c /etc/nats/nats.conf")
        .with_volume_mapping(str(REPO / "docker" / "nats" / config), "/etc/nats/nats.conf", "ro")
        .with_exposed_ports(4222)
        .waiting_for(LogMessageWaitStrategy("Server is ready").with_startup_timeout(30))
    )
    if config == "nats.conf":
        container = container.with_exposed_ports(8222)
    with container as c:
        host = c.get_container_host_ip()
        yield f"nats://app:app@{host}:{c.get_exposed_port(4222)}"


@pytest.fixture(scope="session")
def nats_full(docker_engine: None) -> Iterator[str]:
    """Monitoring port and $SYS both enabled. All five provenance sources reachable."""
    yield from _nats_container("nats.conf")


@pytest.fixture(scope="session")
def nats_bare(docker_engine: None) -> Iterator[str]:
    """No monitoring port, no system account. The honest-empty-state fixture."""
    yield from _nats_container("nats-bare.conf")


@pytest.fixture
async def app_client(database_url: str, nats_full: str, tmp_path_factory: pytest.TempPathFactory):
    """The whole app, with one connected server registered.

    Every domain suite below drives the real HTTP API rather than the services
    directly: the wiring between controller, service and connection manager is
    exactly the part unit tests cannot see, and it is where the bugs have been.
    """
    from urllib.parse import urlsplit

    from litestar.testing import AsyncTestClient

    from nats_lens.app import create_app
    from nats_lens.config import Settings
    from nats_lens.crypto import generate_key

    parts = urlsplit(nats_full)
    # A temp upload directory, always. `Settings` defaults to a path relative to
    # the working directory, so without this an integration run writes uploaded
    # descriptors into the developer's own ./data.
    settings = Settings(
        database_url=database_url,
        secret_key=generate_key(),
        debug=False,
        proto_upload_dir=tmp_path_factory.mktemp("protos"),
    )

    async with AsyncTestClient(app=create_app(settings)) as client:
        created = await client.post(
            "/api/servers",
            json={
                "name": f"it-{uuid.uuid4().hex[:8]}",
                "urls": [f"nats://{parts.hostname}:{parts.port}"],
                "auth_mode": "userpass",
                "username": "app",
                "secrets": [{"kind": "password", "value": "app"}],
            },
        )
        assert created.status_code == 201, created.text
        server_id = created.json()["id"]

        connected = await client.post(f"/api/servers/{server_id}/connect")
        assert connected.status_code == 201, connected.text
        assert connected.json()["state"] == "connected"

        # Carried on the client so every test can reach it without a second
        # fixture in each signature. `AsyncTestClient` has no such attribute, and
        # is not generic over one, so the checker is told rather than fought.
        client.server_id = server_id  # ty: ignore[unresolved-attribute]
        yield client


@pytest.fixture
async def descriptor(app_client) -> AsyncIterator[str]:
    """A registered protobuf descriptor, and the full name of its one message.

    Built here rather than checked in: a FileDescriptorSet is opaque bytes, and a
    fixture that constructs it says what is in it.
    """
    import base64

    from google.protobuf import descriptor_pb2

    # descriptor_pb2 is generated at import time, so its members are invisible to
    # a static checker even though they are certainly there at runtime.
    file = descriptor_pb2.FileDescriptorProto(  # ty: ignore[unresolved-attribute]
        name="it_fixture.proto", package="it.fixture", syntax="proto3"
    )
    message = file.message_type.add(name="Sample")
    message.field.add(name="id", number=1, type=9, label=1)  # string, optional
    message.field.add(name="total", number=2, type=5, label=1)  # int32, optional

    bundle = descriptor_pb2.FileDescriptorSet(file=[file])  # ty: ignore[unresolved-attribute]
    uploaded = await app_client.post(
        "/api/schemas/descriptors",
        json={
            "filename": "it_fixture.pb",
            "content_b64": base64.b64encode(bundle.SerializeToString()).decode(),
            "is_descriptor_set": True,
        },
    )
    assert uploaded.status_code == 201, uploaded.text

    full_name = "it.fixture.Sample"
    yield full_name

    await app_client.delete(f"/api/schemas/descriptors/{uploaded.json()['id']}")
