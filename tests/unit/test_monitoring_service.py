"""The rule that matters: a figure nats-lens could not see is never a zero.

Every other test in this suite is about reading a monitoring port correctly.
These are about the case where there is nothing to read -- no URL configured, or
a port that refuses -- because that is the case a dashboard normally gets wrong,
and getting it right is the reason this product exists.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import msgspec
import pytest

from nats_lens.conn.monitoring import MonitoringClient
from nats_lens.conn.poller import MonitorPoller, MonitorSnapshot, Tab, rates_between
from nats_lens.domain.monitor.schemas import ConnzQuery, HealthQuery
from nats_lens.domain.monitor.service import (
    MonitorView,
    compact,
    health_battery,
    human_bytes,
    prometheus_hint,
    spaced_uptime,
    varz_rows,
)
from nats_lens.provenance import Reason, Source

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GOLDEN = FIXTURES / "golden" / "monitor.json"
URL = "http://nats-1.prod.us-east:8222"
FIXED_AT = "2026-09-05T12:04:19Z"


def payload(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


def serve_everything(request: httpx2.Request) -> httpx2.Response:
    name = request.url.path.lstrip("/")
    if name == "healthz":
        variants = json.loads(payload("healthz"))
        if request.url.params.get("consumer"):
            return httpx2.Response(503, json=variants["consumer_not_current"])
        return httpx2.Response(200, json=variants["ok"])
    return httpx2.Response(200, content=payload(name))


def poller(handler=serve_everything) -> MonitorPoller:
    return MonitorPoller(
        uuid.uuid4(),
        URL,
        5.0,
        client=MonitoringClient(URL, transport=httpx2.MockTransport(handler)),
    )


async def a_watched_poller() -> MonitorPoller:
    """A poller with both tabs open and two samples behind it, ready to report."""
    p = poller()
    p.note_interest(Tab.CONNECTIONS)
    p.note_interest(Tab.ROUTES)
    await p.poll_once()
    await p.poll_once()
    return p


# ------------------------------------------------------------------ the empty states


def test_a_server_with_no_monitoring_url_names_the_fix_instead_of_showing_zero() -> None:
    view = MonitorView(None, None, poll_seconds=5.0)

    for sourced in (view.varz(), view.traffic(), view.jetstream(), view.rates()):
        assert sourced.value is None, "a zero here is the one bug that would kill this product"
        assert sourced.unavailable is not None
        assert sourced.unavailable.reason is Reason.MONITORING_NOT_CONFIGURED
        assert sourced.unavailable.fix
        assert "http_port" in sourced.unavailable.fix
        assert sourced.unavailable.doc


async def test_an_unreachable_monitoring_url_yields_absences_carrying_the_errno() -> None:
    """The acceptance case: the URL is set, the port is dead, nothing is invented."""

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request) from OSError(61, "Connection refused")

    p = poller(refuse)
    try:
        snapshot = await p.poll_once()
    finally:
        await p.aclose()

    view = MonitorView(URL, snapshot, poll_seconds=5.0)

    for sourced in (view.varz(), view.traffic(), view.jetstream(), view.rates()):
        assert sourced.value is None
        assert sourced.unavailable is not None
        assert sourced.unavailable.reason is Reason.MONITORING_UNREACHABLE
        assert sourced.unavailable.fix
        assert "[Errno 61]" in sourced.unavailable.fix
        assert "Connection refused" in sourced.unavailable.fix


def test_traffic_keeps_its_source_badge_when_it_is_absent() -> None:
    """The badge is how the UI knows which empty state to draw."""
    assert MonitorView(None, None).traffic().source is Source.MONITOR
    assert MonitorView(None, None).rates().source is Source.SAMPLED


async def test_an_overview_of_a_dead_port_is_empty_and_says_why() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    p = poller(refuse)
    try:
        snapshot = await p.poll_once()
    finally:
        await p.aclose()

    overview = MonitorView(URL, snapshot, poll_seconds=5.0).overview()

    assert overview.reachable is False
    assert overview.varz is None
    assert overview.jsz is None
    assert overview.rates is None
    assert overview.varz_rows == ()
    assert overview.error
    assert "monitoring URL is set but did not answer" in overview.error
    assert overview.url == URL, "the URL that failed is still worth showing"


def test_the_overview_of_an_unconfigured_server_carries_the_other_fix() -> None:
    overview = MonitorView("", None, poll_seconds=5.0).overview()
    assert overview.reachable is False
    assert overview.error
    assert "-m 8222" in overview.error


# ------------------------------------------------------------------ the good path


async def test_a_reachable_port_produces_sourced_monitor_values() -> None:
    p = await a_watched_poller()
    try:
        view = MonitorView(URL, p.latest, poll_seconds=5.0, rates=p.rates)

        varz = view.varz()
        assert varz.value is not None
        assert varz.source is Source.MONITOR
        assert varz.value.connections == 128

        traffic = view.traffic()
        assert traffic.value is not None
        assert traffic.value.slow_consumers == 2
        assert traffic.value.subscriptions == 2140

        jsz = view.jetstream()
        assert jsz.value is not None
        assert jsz.value.meta_leader == "nats-1"
    finally:
        await p.aclose()


async def test_rates_are_sampled_and_never_presented_as_a_server_total() -> None:
    p = await a_watched_poller()
    try:
        rates = MonitorView(URL, p.latest, rates=p.rates).rates()
        assert rates.value is not None
        assert rates.source is Source.SAMPLED, "no server ever published a per-second figure"
        assert rates.value.window_ms > 0
    finally:
        await p.aclose()


async def test_the_overview_renders_the_designs_cards() -> None:
    p = await a_watched_poller()
    try:
        overview = MonitorView(URL, p.latest, 5.0, rates=p.rates).overview()
    finally:
        await p.aclose()

    assert overview.reachable is True
    assert overview.status_code == 200
    assert overview.latency_ms is not None
    assert overview.poll_seconds == 5.0
    assert overview.error is None

    rows = {row.k: row.v for row in overview.varz_rows}
    assert rows["Uptime"] == "21d 4h 12m 8s"
    assert rows["Connections"] == "128"
    assert rows["Total connections"] == "4.1M"
    assert rows["Slow consumers"] == "2"
    assert rows["Messages in"] == "9.8B"
    assert rows["Messages out"] == "31.2B"
    assert rows["Bytes in"] == "1.9 TB"
    assert rows["CPU"] == "18% of 8 cores"


def test_jetstream_turned_off_names_the_jetstream_fix_not_the_monitoring_one() -> None:
    """`/jsz` answers 200 with `disabled: true`. That is a different problem."""
    from nats_lens.domain.monitor.schemas import JszSummary, VarzSummary

    varz = msgspec.json.decode(
        msgspec.json.encode(
            {
                "server_id": "x",
                "server_name": "bare",
                "version": "2.11.4",
                "uptime": "1h",
                "start": "s",
                "connections": 1,
                "total_connections": 1,
                "routes": 0,
                "remotes": 0,
                "leafnodes": 0,
                "subscriptions": 1,
                "slow_consumers": 0,
                "in_msgs": 0,
                "out_msgs": 0,
                "in_bytes": 0,
                "out_bytes": 0,
                "mem": 1,
                "cpu": 0.0,
                "cores": 1,
                "max_payload": 1,
                "jetstream_enabled": False,
            }
        ),
        type=VarzSummary,
    )
    jsz = JszSummary(
        meta_leader=None,
        streams=0,
        consumers=0,
        messages=0,
        bytes=0,
        memory=0,
        storage=0,
        api_total=0,
        api_errors=0,
        disabled=True,
    )
    snapshot = MonitorSnapshot(at=datetime.now(UTC), monotonic=0.0, varz=varz, jsz=jsz)
    view = MonitorView(URL, snapshot)

    jetstream = view.jetstream()
    assert jetstream.value is None
    assert jetstream.unavailable is not None
    assert jetstream.unavailable.reason is Reason.JETSTREAM_NOT_ENABLED
    assert view.overview().jsz is None, "a disabled JetStream is not a JetStream full of zeros"
    assert view.overview().reachable is True


# ------------------------------------------------------------------ health and hints


def test_the_health_battery_is_the_three_standard_probes_plus_what_was_asked_for() -> None:
    battery = health_battery(HealthQuery())
    assert len(battery) == 3
    assert battery[1].js_enabled_only
    assert battery[2].js_server_only

    with_stream = health_battery(HealthQuery(stream="TELEMETRY", consumer="cold-archive"))
    assert len(with_stream) == 5
    assert with_stream[3].stream == "TELEMETRY" and with_stream[3].consumer is None
    assert with_stream[4].consumer == "cold-archive"

    single = health_battery(HealthQuery(js_enabled_only=True))
    assert len(single) == 1, "an explicit probe is one question and gets one row"


async def test_the_health_tab_shows_a_503_as_a_row() -> None:
    p = poller()
    try:
        checks = await p.fetch_health(
            health_battery(HealthQuery(stream="TELEMETRY", consumer="cold-archive"))
        )
    finally:
        await p.aclose()

    assert [c.status_code for c in checks] == [200, 200, 200, 200, 503]
    assert checks[-1].ok is False
    assert checks[-1].error and "cold-archive" in checks[-1].error
    assert all(c.path.startswith("/healthz") for c in checks)


def test_the_prometheus_hint_points_at_the_port_this_server_actually_has() -> None:
    hint = prometheus_hint(URL)
    assert URL in hint.exporter_command
    assert hint.scrape_url == "http://nats-1.prod.us-east:7777/metrics"
    assert "surveyor" in hint.surveyor_note
    assert hint.grafana_dashboard_url.startswith("https://")


def test_the_prometheus_hint_is_answered_even_without_a_monitoring_url() -> None:
    """Advice about what to run, not a reading. The user with neither needs it most."""
    hint = prometheus_hint(None)
    assert hint.exporter_image
    assert "your-nats-host" in hint.exporter_command


# ------------------------------------------------------------------ formatting


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "0"), (2, "2"), (2140, "2,140"), (4_120_338, "4.1M"), (9_800_124_553, "9.8B")],
)
def test_compact(value: int, expected: str) -> None:
    assert compact(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "0 B"), (12288, "12.0 KB"), (3_650_722_816, "3.4 GB"), (2_100_338_844_192, "1.9 TB")],
)
def test_human_bytes(value: int, expected: str) -> None:
    assert human_bytes(value) == expected


def test_spaced_uptime() -> None:
    assert spaced_uptime("21d4h12m8s") == "21d 4h 12m 8s"
    assert spaced_uptime("") == ""


def test_varz_rows_never_invent_a_figure() -> None:
    """Every card is a number /varz published, formatted. Nothing is derived."""
    from nats_lens.conn.monitoring import to_varz_summary

    varz = to_varz_summary(msgspec.json.decode(payload("varz"), type=_raw_varz()))
    rows = varz_rows(varz)
    assert len(rows) == 12
    assert len({row.k for row in rows}) == 12


def _raw_varz():
    from nats_lens.conn.monitoring import RawVarz

    return RawVarz


# ------------------------------------------------------------------ the golden file


def _normalise(value):
    """Timestamps move; the shape must not. Pin `at` so the golden file is stable."""
    if isinstance(value, dict):
        return {k: (FIXED_AT if k == "at" else _normalise(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    if isinstance(value, float):
        return round(value, 3)
    return value


def golden_rates(varz):
    """A five-second window with the design's figures, rather than the clock's.

    Two back-to-back polls of the same fixture would produce a window of however
    many milliseconds the test machine took and a rate of zero, which is neither
    stable nor useful to mock against. So the earlier reading is synthesised from
    the later one at the throughput the design shows.
    """
    return rates_between(
        msgspec.structs.replace(
            varz,
            in_msgs=varz.in_msgs - 6020,
            out_msgs=varz.out_msgs - 19905,
            in_bytes=varz.in_bytes - 22_000_000,
            out_bytes=varz.out_bytes - 71_000_000,
        ),
        varz,
        window_ms=5000,
    )


async def build_golden() -> dict:
    """The payloads the Monitor routes return, for the frontend to mock against."""
    p = await a_watched_poller()
    try:
        assert p.latest is not None
        view = MonitorView(URL, p.latest, 5.0, rates=golden_rates(p.latest.varz))
        connections = await p.fetch_connections(
            ConnzQuery(sort="pending_bytes", limit=50, subs=True)
        )
        routes = await p.fetch_routes()
        health = await p.fetch_health(
            health_battery(HealthQuery(stream="TELEMETRY", consumer="cold-archive"))
        )
        unreachable = MonitorView(
            URL,
            MonitorSnapshot(
                at=datetime.now(UTC),
                monotonic=0.0,
                error="ConnectError: [Errno 61] Connection refused.",
            ),
            5.0,
        )
        document = {
            "overview": view.overview(),
            "connections": connections,
            "routes": routes,
            "health": health,
            "prometheus": prometheus_hint(URL),
            "unavailable": {
                "overview": unreachable.overview(),
                "traffic": unreachable.traffic(),
                "not_configured": MonitorView(None, None, 5.0).traffic(),
            },
        }
    finally:
        await p.aclose()

    # Latency is a measurement of this machine; the frontend mocks a shape, not a
    # stopwatch. Pinning it keeps the file from changing on every run.
    encoded = _normalise(json.loads(msgspec.json.encode(document)))
    return _pin_latency(encoded)


def _pin_latency(value):
    if isinstance(value, dict):
        return {
            k: (41.0 if k == "latency_ms" and v is not None else _pin_latency(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_pin_latency(v) for v in value]
    return value


async def test_the_golden_fixture_still_matches_what_the_code_produces() -> None:
    """The interface with the frontend is this file, not a conversation.

    F2 builds its mocks from it, so it is regenerated from the real structs rather
    than hand-maintained, and drifting from the code fails here.
    """
    assert GOLDEN.exists(), f"missing golden fixture: {GOLDEN}"
    assert json.loads(GOLDEN.read_text()) == await build_golden()
