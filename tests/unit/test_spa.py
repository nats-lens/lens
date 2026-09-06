"""How the production image serves the built SPA.

This is subtle enough to have been wrong twice: a catch-all outranks Litestar's
static router (so every asset came back as index.html, a blank page whose only
symptom was a 407-byte "bundle"), and `/{path:path}` does not match the bare
root (so "/" itself 404'd). Neither is visible in development, because Vite
serves the SPA there and none of this code runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from litestar.testing import TestClient

from nats_lens.app import create_app
from nats_lens.config import Settings
from nats_lens.crypto import generate_key

pytestmark = pytest.mark.unit

INDEX = b"<!doctype html><title>nats-lens</title>"
BUNDLE = b"console.log('the real bundle')"


def _migrated_db(tmp_path: Path) -> str:
    """A real file with the schema on it.

    The connection manager reads the server table during lifespan, so an empty
    in-memory database fails startup before any of this is exercised.
    """
    import asyncio

    from nats_lens.db.models import Base
    from nats_lens.db.session import make_engine

    url = f"sqlite+aiosqlite:///{tmp_path / 'registry.db'}"

    async def create() -> None:
        engine = make_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(create())
    return url


@pytest.fixture
def spa_client(tmp_path: Path) -> TestClient:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_bytes(INDEX)
    (static / "assets" / "app.js").write_bytes(BUNDLE)
    (static / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    (static / "site.webmanifest").write_bytes(b'{"name":"nats-lens"}')

    settings = Settings(
        database_url=_migrated_db(tmp_path),
        secret_key=generate_key(),
        static_dir=static,
    )
    return TestClient(app=create_app(settings))


def test_the_root_serves_the_app(spa_client: TestClient) -> None:
    with spa_client as client:
        response = client.get("/")
    assert response.status_code == 200
    assert response.content == INDEX


@pytest.mark.parametrize(
    "route", ["/jetstream", "/kv", "/objects", "/monitor", "/servers/new", "/schemas", "/core"]
)
def test_every_client_side_route_falls_back_to_the_app(spa_client: TestClient, route: str) -> None:
    """A reloaded deep link must reach the router, not a 404."""
    with spa_client as client:
        response = client.get(route)
    assert response.status_code == 200
    assert response.content == INDEX


def test_assets_are_served_as_themselves(spa_client: TestClient) -> None:
    """The regression that mattered: the bundle must be the bundle."""
    with spa_client as client:
        response = client.get("/assets/app.js")
    assert response.status_code == 200
    assert response.content == BUNDLE
    assert "javascript" in response.headers["content-type"]
    assert response.content != INDEX


def test_the_api_still_wins(spa_client: TestClient) -> None:
    with spa_client as client:
        assert client.get("/api/health").status_code == 200
        # An unknown API path is a 404, not the app: an XHR that silently receives
        # HTML is far harder to diagnose than one that receives a status code.
        missing = client.get("/api/nope")
    assert missing.status_code == 404
    assert missing.content != INDEX


def test_path_traversal_cannot_escape_the_static_root(spa_client: TestClient) -> None:
    with spa_client as client:
        response = client.get("/../../../etc/passwd")
    assert response.status_code in (200, 404)
    assert b"root:" not in response.content


def test_without_a_static_dir_nothing_is_mounted(tmp_path: Path) -> None:
    """In development Vite serves the SPA, so / must stay a plain 404."""
    settings = Settings(database_url=_migrated_db(tmp_path), secret_key=generate_key())
    with TestClient(app=create_app(settings)) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/health").status_code == 200


def test_the_favicon_is_served_with_its_own_type(spa_client: TestClient) -> None:
    """A browser asks for /favicon.ico whether or not the page links to it."""
    with spa_client as client:
        response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.content != INDEX
    assert "icon" in response.headers["content-type"]


def test_the_manifest_is_served_as_a_manifest(spa_client: TestClient) -> None:
    with spa_client as client:
        response = client.get("/site.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")


def test_only_fingerprinted_assets_are_cached_forever(spa_client: TestClient) -> None:
    """Vite hashes what it writes into /assets; nothing else has a new name on
    a new build, so nothing else may be immutable -- a replaced favicon would
    otherwise stay replaced only for people who had never loaded the old one."""
    with spa_client as client:
        bundle = client.get("/assets/app.js")
        favicon = client.get("/favicon.ico")
        index = client.get("/")

    assert "immutable" in bundle.headers["cache-control"]
    assert "immutable" not in favicon.headers["cache-control"]
    assert index.headers["cache-control"] == "no-cache"
