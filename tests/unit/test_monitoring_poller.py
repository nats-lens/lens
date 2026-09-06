"""The poller: two samples, rates from the gap between them, and giving up loudly.

nats-lens keeps no time series. That is not an omission to be fixed later -- it
is the reason the Monitor screen can promise that every rate on it was measured
between two polls it can name, and the reason the Prometheus card exists. These
tests hold that line.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest

from nats_lens.conn.monitoring import MonitoringClient
from nats_lens.conn.poller import (
    BREAKER_BACKOFF_SECONDS,
    BREAKER_THRESHOLD,
    MonitorPoller,
    MonitorSnapshot,
    Tab,
    rates_between,
)
from nats_lens.domain.monitor.schemas import ConnzQuery, VarzSummary

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def payload(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


def varz_at(in_msgs: int, out_msgs: int, in_bytes: int, out_bytes: int, start: str) -> VarzSummary:
    return VarzSummary(
        server_id="NDHJZ",
        server_name="nats-1",
        version="2.11.4",
        uptime="1h0m0s",
        start=start,
        connections=128,
        total_connections=4_120_338,
        routes=2,
        remotes=2,
        leafnodes=1,
        subscriptions=2140,
        slow_consumers=2,
        in_msgs=in_msgs,
        out_msgs=out_msgs,
        in_bytes=in_bytes,
        out_bytes=out_bytes,
        mem=3_650_722_816,
        cpu=18.0,
        cores=8,
        max_payload=1_048_576,
        jetstream_enabled=True,
    )


def snapshot(
    varz: VarzSummary | None, monotonic: float, error: str | None = None
) -> MonitorSnapshot:
    return MonitorSnapshot(at=datetime.now(UTC), monotonic=monotonic, varz=varz, error=error)


def poller(handler, poll_seconds: float = 5.0) -> MonitorPoller:
    return MonitorPoller(
        uuid.uuid4(),
        "http://nats-1.prod.us-east:8222",
        poll_seconds,
        client=MonitoringClient(
            "http://nats-1.prod.us-east:8222", transport=httpx2.MockTransport(handler)
        ),
    )


def serve_everything(request: httpx2.Request) -> httpx2.Response:
    name = request.url.path.lstrip("/")
    if name == "healthz":
        return httpx2.Response(200, json=json.loads(payload("healthz"))["ok"])
    return httpx2.Response(200, content=payload(name))


# ------------------------------------------------------------------ rates


def test_two_samples_over_a_known_window_give_the_expected_per_second_figures() -> None:
    """The whole arithmetic behind the `sampled` badge, in one assertion.

    10 seconds apart, 12,040 messages in and 39,810 out, so 1,204/s and 3,981/s --
    the figures the design's legend shows.
    """
    previous = varz_at(1_000_000, 5_000_000, 400_000_000, 900_000_000, start="S")
    latest = varz_at(1_012_040, 5_039_810, 444_000_000, 990_000_000, start="S")

    rates = rates_between(previous, latest, window_ms=10_000)

    assert rates is not None
    assert rates.in_msgs_per_sec == pytest.approx(1204.0)
    assert rates.out_msgs_per_sec == pytest.approx(3981.0)
    assert rates.in_bytes_per_sec == pytest.approx(4_400_000.0)
    assert rates.out_bytes_per_sec == pytest.approx(9_000_000.0)
    assert rates.window_ms == 10_000, "the window is part of the figure, not a footnote"


def test_a_window_of_zero_produces_nothing_rather_than_infinity() -> None:
    v = varz_at(1, 1, 1, 1, start="S")
    assert rates_between(v, v, window_ms=0) is None


def test_a_restarted_server_produces_no_rate() -> None:
    """Counters begin again from zero on restart. The spike would not be traffic."""
    previous = varz_at(9_000_000, 9_000_000, 9_000_000, 9_000_000, start="2026-08-15T07:52:11Z")
    latest = varz_at(120, 340, 4_400, 9_100, start="2026-09-05T11:04:02Z")
    assert rates_between(previous, latest, window_ms=5000) is None


def test_a_counter_that_went_backwards_produces_no_rate() -> None:
    previous = varz_at(9_000_000, 9_000_000, 9_000_000, 9_000_000, start="S")
    latest = varz_at(120, 9_000_100, 9_000_100, 9_000_100, start="S")
    assert rates_between(previous, latest, window_ms=5000) is None


async def test_the_poller_keeps_exactly_two_samples() -> None:
    """No time series. Prometheus is where history goes, and the UI says so."""
    p = poller(serve_everything)
    try:
        for _ in range(5):
            await p.poll_once()
        assert len(p._good) == 2
        assert p.last is not None
        assert p.previous is not None
        assert p.previous.monotonic <= p.last.monotonic
    finally:
        await p.aclose()


async def test_rates_need_a_second_poll_before_they_exist() -> None:
    p = poller(serve_everything)
    try:
        await p.poll_once()
        assert p.rates is None, "one reading is a total, not a rate"
        assert p.last is not None
        p._good = (snapshot(p.last.varz, 0.0), snapshot(p.last.varz, 5.0))
        assert p.rates is not None
        assert p.rates.window_ms == 5000
    finally:
        await p.aclose()


# ------------------------------------------------------------------ the breaker


async def test_three_consecutive_failures_open_the_breaker_and_back_off() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request) from OSError(61, "Connection refused")

    p = poller(refuse, poll_seconds=5.0)
    try:
        for expected in range(1, BREAKER_THRESHOLD):
            await p.poll_once()
            assert p.failures == expected
            assert not p.breaker_open
            assert p.interval == 5.0

        last = await p.poll_once()
        assert p.breaker_open
        assert p.interval == BREAKER_BACKOFF_SECONDS
        assert last.reachable is False
        assert last.varz is None, "a server we cannot reach reports nothing, not zeros"
        assert "[Errno 61]" in (last.error or "")
        assert last.status_code == 0
    finally:
        await p.aclose()


async def test_one_good_poll_closes_the_breaker_again() -> None:
    fail = {"on": True}

    def flaky(request: httpx2.Request) -> httpx2.Response:
        if fail["on"]:
            raise httpx2.ConnectError("refused", request=request)
        return serve_everything(request)

    p = poller(flaky)
    try:
        for _ in range(BREAKER_THRESHOLD):
            await p.poll_once()
        assert p.breaker_open

        fail["on"] = False
        await p.poll_once()
        assert p.failures == 0
        assert not p.breaker_open
        assert p.interval == 5.0
    finally:
        await p.aclose()


async def test_a_failed_tick_does_not_discard_the_last_good_sample() -> None:
    """The header goes red; the two samples behind the rate are still the real ones."""
    fail = {"on": False}

    def flaky(request: httpx2.Request) -> httpx2.Response:
        if fail["on"]:
            raise httpx2.ConnectError("refused", request=request)
        return serve_everything(request)

    p = poller(flaky)
    try:
        await p.poll_once()
        good = p.last
        fail["on"] = True
        await p.poll_once()

        assert p.last is good
        assert p.latest is not None
        assert p.latest.reachable is False
    finally:
        await p.aclose()


# ------------------------------------------------------------------ tab interest


async def test_connz_and_routez_are_not_polled_until_someone_is_watching() -> None:
    """Polling a page nobody has open costs the server real work for nothing."""
    seen: list[str] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.path)
        return serve_everything(request)

    p = poller(record)
    try:
        await p.poll_once()
        assert "/varz" in seen
        assert "/jsz" in seen
        assert seen.count("/healthz") == 3, "each health probe is its own request"
        assert "/connz" not in seen
        assert "/routez" not in seen

        seen.clear()
        p.note_interest(Tab.CONNECTIONS)
        p.note_interest(Tab.ROUTES)
        await p.poll_once()

        assert "/connz" in seen
        assert "/routez" in seen
        assert "/leafz" in seen
        assert "/gatewayz" in seen
        assert p.latest is not None
        assert p.latest.connz is not None
        assert p.latest.routez is not None
    finally:
        await p.aclose()


async def test_reading_the_connections_endpoint_registers_the_interest() -> None:
    p = poller(serve_everything)
    try:
        assert not p.is_watching(Tab.CONNECTIONS)
        await p.fetch_connections(ConnzQuery())
        assert p.is_watching(Tab.CONNECTIONS)
    finally:
        await p.aclose()


# ------------------------------------------------------------------ the loop


async def test_an_interval_change_applies_now_rather_than_after_the_current_sleep() -> None:
    """A user who drops the poll to a second expects the screen to speed up.

    Without the wake event the change would land only after the 3600-second sleep
    the loop was already sitting in, which is indistinguishable from it not working.
    """
    ticks = asyncio.Event()
    count = {"n": 0}

    def record(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/varz":
            count["n"] += 1
            if count["n"] >= 2:
                ticks.set()
        return serve_everything(request)

    p = poller(record, poll_seconds=3600.0)
    try:
        p.start()
        while count["n"] < 1:
            await asyncio.sleep(0.01)

        p.reconfigure(p.base_url, 0.5)
        await asyncio.wait_for(ticks.wait(), timeout=3.0)
        assert p.poll_seconds == 0.5
    finally:
        await p.aclose()


async def test_the_loop_survives_a_port_that_never_answers() -> None:
    """A poller that dies on the first failure leaves the UI showing stale numbers."""

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    p = poller(refuse, poll_seconds=0.5)
    try:
        p.start()
        while p.failures < 2:
            await asyncio.sleep(0.01)
        assert p._task is not None
        assert not p._task.done()
    finally:
        await p.aclose()
