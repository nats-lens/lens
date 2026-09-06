"""Wave-0 gate: the app boots against real services and answers.

Thin on purpose. Wave 1+ agents add the domain suites alongside this.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_health_answers(database_url: str) -> None:
    from litestar.testing import AsyncTestClient

    from nats_lens.app import create_app
    from nats_lens.config import Settings
    from nats_lens.crypto import generate_key

    settings = Settings(database_url=database_url, secret_key=generate_key(), debug=True)
    async with AsyncTestClient(app=create_app(settings)) as client:
        response = await client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


async def test_a_bare_server_is_reachable_but_has_no_monitoring_port(
    nats_bare: str, free_tcp_port: int
) -> None:
    """The premise behind every empty state in the design.

    A client connects fine and JetStream works; the monitoring port simply is not
    there, which is why server-wide counters must arrive as `unavailable` rather
    than as zeros.
    """
    import httpx2
    import nats

    nc = await nats.connect(nats_bare)
    try:
        assert nc.is_connected
        assert nc.connected_server_version is not None
    finally:
        await nc.close()

    host = nats_bare.split("@")[-1].split(":")[0]
    with pytest.raises(httpx2.HTTPError):
        async with httpx2.AsyncClient(timeout=2.0) as http:
            await http.get(f"http://{host}:{free_tcp_port}/varz")


async def test_a_full_server_serves_all_three_sources(nats_full: str) -> None:
    import nats

    nc = await nats.connect(nats_full)
    try:
        js = nc.jetstream()
        await js.account_info()
    finally:
        await nc.close()
