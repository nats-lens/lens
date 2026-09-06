"""The monitoring client, against captured payloads and against a dead port.

The fixtures under `tests/fixtures/` are hand-written to the shapes nats-server
2.11 publishes, because Docker was unavailable when this suite was written
(`docker info` refused). They are faithful to the documented field names and
types rather than captured verbatim, so if a real capture ever disagrees, the
capture wins and these files should be replaced with it.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import msgspec
import pytest

from nats_lens.conn.monitoring import (
    MonitoringClient,
    MonitoringError,
    describe_error,
    display_path,
    to_connz_page,
    to_jsz_summary,
    to_routez_summary,
    to_varz_summary,
)
from nats_lens.domain.monitor.schemas import ConnzQuery, HealthQuery

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ENDPOINTS = ("varz", "connz", "routez", "leafz", "gatewayz", "jsz", "healthz")


def payload(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


def _serve(request: httpx2.Request) -> httpx2.Response:
    """A monitoring port that answers every endpoint from the captured payloads.

    `/healthz` answers 200 unless the query names the lagging consumer the design
    shows, which is the one row on the Health tab that is not green.
    """
    name = request.url.path.lstrip("/")
    if name == "healthz":
        variants = json.loads(payload("healthz"))
        if request.url.params.get("consumer"):
            return httpx2.Response(503, json=variants["consumer_not_current"])
        return httpx2.Response(200, json=variants["ok"])
    return httpx2.Response(200, content=payload(name))


def client(handler=_serve) -> MonitoringClient:
    return MonitoringClient(
        "http://nats-1.prod.us-east:8222", transport=httpx2.MockTransport(handler)
    )


# ------------------------------------------------------------------ parsing


@pytest.mark.parametrize("name", ENDPOINTS)
def test_every_captured_payload_is_valid_json(name: str) -> None:
    assert json.loads(payload(name))


async def test_varz_carries_the_counters_a_client_cannot_see() -> None:
    async with client() as c:
        fetched = await c.varz()
    varz = to_varz_summary(fetched.value)

    assert varz.server_name == "nats-1"
    assert varz.connections == 128
    assert varz.total_connections == 4_120_338
    assert varz.subscriptions == 2140
    assert varz.slow_consumers == 2
    assert varz.routes == 2
    assert varz.leafnodes == 1
    assert varz.in_msgs == 9_800_124_553
    assert varz.out_bytes == 6_800_112_934_418
    assert varz.jetstream_enabled is True
    assert fetched.result.ok
    assert fetched.result.status_code == 200
    assert fetched.result.latency_ms >= 0


async def test_varz_without_a_jetstream_config_is_not_jetstream_enabled() -> None:
    """A server built with JetStream but started without it still emits the key."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"server_name": "bare", "jetstream": {}})

    async with client(handler) as c:
        fetched = await c.varz()
    assert to_varz_summary(fetched.value).jetstream_enabled is False


async def test_connz_preserves_paging_and_subject_lists() -> None:
    async with client() as c:
        fetched = await c.connz(ConnzQuery(subs=True, limit=50))
    page = to_connz_page(fetched.value)

    assert page.total == 128, "the server's own total, not the length of this page"
    assert page.num_connections == 7
    assert page.limit == 50
    assert page.offset == 0
    assert len(page.connections) == 7

    first = page.connections[0]
    assert first.cid == 9412
    assert first.name == "analytics-tap"
    assert first.account == "APP"
    assert first.pending_bytes == 2_516_582
    assert first.rtt == "1.2ms"
    assert first.lang == "go"
    assert "events.>" in first.subjects


async def test_routez_merges_routes_leafs_and_gateways_into_one_table() -> None:
    async with client() as c:
        routez = (await c.routez()).value
        leafz = (await c.leafz()).value
        gatewayz = (await c.gatewayz()).value
    summary = to_routez_summary(routez, leafz, gatewayz)

    assert summary.num_routes == 2
    assert summary.num_leafnodes == 1
    assert summary.num_gateways == 0
    kinds = [r.kind for r in summary.routes]
    assert kinds == ["route", "route", "leaf"]

    leaf = summary.routes[-1]
    assert leaf.remote_id == "edge-ap-southeast"
    assert leaf.subscriptions == 34
    assert leaf.rid == 0, "/leafz publishes no connection id; inventing one would be a lie"


async def test_routez_alone_still_produces_a_table() -> None:
    """Older servers do not serve /leafz or /gatewayz. Routes are still worth showing."""
    async with client() as c:
        routez = (await c.routez()).value
    summary = to_routez_summary(routez)
    assert summary.num_leafnodes == 0
    assert summary.num_gateways == 0
    assert len(summary.routes) == 2


async def test_jsz_summarises_the_meta_cluster_and_the_api_counters() -> None:
    async with client() as c:
        fetched = await c.jsz(streams=True, consumers=True)
    jsz = to_jsz_summary(fetched.value)

    assert jsz.meta_leader == "nats-1"
    assert jsz.streams == 5
    assert jsz.consumers == 93
    assert jsz.messages == 78_412_003
    assert jsz.api_total == 84_200_311
    assert jsz.api_errors == 12
    assert jsz.disabled is False


# ------------------------------------------------------------------ the query surfaces


async def test_the_connz_query_surface_reaches_the_wire() -> None:
    """sort, limit, offset, subs, auth, acc and state are the point of /connz."""
    seen: list[httpx2.URL] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url)
        return httpx2.Response(200, content=payload("connz"))

    async with client(handler) as c:
        await c.connz(
            ConnzQuery(
                sort="pending_bytes",
                limit=50,
                offset=100,
                subs=True,
                auth=True,
                account="APP",
                state="closed",
            )
        )

    params = seen[0].params
    assert params["sort"] == "pending_bytes"
    assert params["limit"] == "50"
    assert params["offset"] == "100"
    assert params["subs"] == "true"
    assert params["auth"] == "true"
    assert params["acc"] == "APP", "/connz spells the account filter `acc`"
    assert params["state"] == "closed"


async def test_the_healthz_query_surface_reaches_the_wire() -> None:
    seen: list[httpx2.URL] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url)
        return httpx2.Response(200, json={"status": "ok"})

    async with client(handler) as c:
        await c.healthz(HealthQuery(js_enabled_only=True))
        await c.healthz(HealthQuery(js_server_only=True))
        await c.healthz(HealthQuery(stream="ORDERS", consumer="cold-archive"))

    assert seen[0].params["js-enabled-only"] == "true"
    assert seen[1].params["js-server-only"] == "true"
    assert seen[2].params["stream"] == "ORDERS"
    assert seen[2].params["consumer"] == "cold-archive"


async def test_the_jsz_query_surface_reaches_the_wire() -> None:
    seen: list[httpx2.URL] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url)
        return httpx2.Response(200, content=payload("jsz"))

    async with client(handler) as c:
        await c.jsz(streams=True, consumers=True, accounts=True)

    params = seen[0].params
    assert params["streams"] == "true"
    assert params["consumers"] == "true"
    assert params["accounts"] == "true"


def test_display_path_shows_the_request_that_was_actually_made() -> None:
    assert display_path("/varz") == "/varz"
    shown = display_path("/connz", {"sort": "pending_bytes", "limit": 50, "subs": "true"})
    assert shown == "/connz?sort=pending_bytes&limit=50&subs=true"


# ------------------------------------------------------------------ health


async def test_a_503_from_healthz_is_a_result_not_an_exception() -> None:
    """The design shows a 503 row for a lagging consumer as normal output.

    503 is what an orchestrator reads to decide whether to send traffic here, so
    it is a health answer -- raising on it would hide the one row on the tab that
    the operator actually needs to see.
    """
    async with client() as c:
        check = await c.healthz(HealthQuery(stream="TELEMETRY", consumer="cold-archive"))

    assert check.status_code == 503
    assert check.ok is False
    assert check.status == "error"
    assert check.error is not None
    assert "cold-archive" in check.error
    assert check.path == "/healthz?stream=TELEMETRY&consumer=cold-archive"
    assert check.label == "consumer cold-archive on stream TELEMETRY"
    assert check.latency_ms >= 0


async def test_a_503_with_a_single_error_field_is_read_too() -> None:
    variants = json.loads(payload("healthz"))

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503, json=variants["js_not_enabled"])

    async with client(handler) as c:
        check = await c.healthz()

    assert check.ok is False
    assert check.status == "unavailable"
    assert check.error == "JetStream is not enabled"


async def test_a_healthz_body_that_is_not_json_still_produces_a_row() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503, text="Service Unavailable")

    async with client(handler) as c:
        check = await c.healthz()

    assert check.ok is False
    assert check.error == "Service Unavailable"


# ------------------------------------------------------------------ failures


async def test_a_refused_connection_reports_the_errno() -> None:
    """The errno is the difference between a closed port and a wrong hostname."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("All connection attempts failed", request=request) from OSError(
            61, "Connection refused"
        )

    async with client(handler) as c:
        with pytest.raises(MonitoringError) as caught:
            await c.varz()

    result = caught.value.result
    assert result.ok is False
    assert result.status_code == 0, "no response means no status; 200 would be a lie"
    assert result.latency_ms >= 0, "a failed call is still a measured call"
    assert result.path == "/varz"
    assert "[Errno 61]" in caught.value.detail
    assert "Connection refused" in caught.value.detail


async def test_a_timeout_is_reported_by_name() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timed out", request=request)

    async with client(handler) as c:
        with pytest.raises(MonitoringError) as caught:
            await c.jsz()

    assert "ReadTimeout" in caught.value.detail


async def test_a_non_200_repeats_what_the_port_said() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, text="Unauthorized")

    async with client(handler) as c:
        with pytest.raises(MonitoringError) as caught:
            await c.varz()

    assert caught.value.result.status_code == 401
    assert "Unauthorized" in caught.value.detail


async def test_a_body_that_is_not_the_expected_json_is_named_as_such() -> None:
    """Something answered on 8222. Saying what went wrong beats a stack trace."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text="<html>nginx</html>")

    async with client(handler) as c:
        with pytest.raises(MonitoringError) as caught:
            await c.varz()

    assert caught.value.result.status_code == 200
    assert caught.value.result.ok is False
    assert "not the JSON" in caught.value.detail


def test_describe_error_falls_back_to_the_exception_text() -> None:
    assert describe_error(httpx2.ConnectError("no route to host")).endswith("no route to host.")
    assert describe_error(RuntimeError()) == "RuntimeError."


def test_describe_error_survives_a_cyclic_cause_chain() -> None:
    """Chained exceptions can point back at each other. The walk is bounded."""
    a = RuntimeError("a")
    b = RuntimeError("b")
    a.__cause__ = b
    b.__cause__ = a
    assert describe_error(a) == "RuntimeError: a."


def test_the_raw_structs_ignore_fields_a_newer_server_adds() -> None:
    """nats-server gains keys between releases. Breaking on one would be absurd."""
    from nats_lens.conn.monitoring import RawVarz

    raw = msgspec.json.decode(
        b'{"server_name": "nats-9", "some_future_field": {"nested": true}}', type=RawVarz
    )
    assert raw.server_name == "nats-9"
