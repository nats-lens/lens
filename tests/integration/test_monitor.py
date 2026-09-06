"""The monitoring client against a real nats-server, and against one without a port.

The unit suite proves the parsing and the empty states against captured payloads.
This proves the captures were right: that a real server's `/varz` still has the
fields `VarzSummary` reads, that a real `/healthz` really does answer 503 for a
stream that is not there, and that a real closed port really does surface an
errno rather than a stack trace.

`nats_bare` -- no monitoring port at all -- is the one that matters most. It is
what turns "we show the fix instead of a zero" into something that fails when it
stops being true.
"""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import urlsplit

import pytest

from nats_lens.conn.monitoring import (
    MonitoringClient,
    MonitoringError,
    to_connz_page,
    to_jsz_summary,
    to_routez_summary,
    to_varz_summary,
)
from nats_lens.domain.monitor.schemas import ConnzQuery, HealthQuery
from nats_lens.domain.monitor.service import MonitorView, health_battery
from nats_lens.provenance import Reason, Source

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture(scope="module")
def monitored_server(nats_image: str) -> Iterator[tuple[str, str]]:
    """A JetStream server, as `(monitoring_url, client_url)`.

    Both are yielded because some of this suite has to *cause* what it then reads
    -- a subscription is only visible in `/subsz` if it was made against this
    same server, and querying one container about another's clients quietly
    returns nothing.

    Its own container rather than the session-scoped `nats_full`, which does not
    expose the mapped 8222 this suite needs.
    """
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy

    container = (
        DockerContainer(nats_image)
        .with_command("-js -m 8222 --server_name nats-monitor")
        .with_exposed_ports(4222, 8222)
        .waiting_for(LogMessageWaitStrategy("Server is ready").with_startup_timeout(30))
    )
    with container as c:
        host = c.get_container_host_ip()
        yield (
            f"http://{host}:{c.get_exposed_port(8222)}",
            f"nats://{host}:{c.get_exposed_port(4222)}",
        )


@pytest.fixture(scope="module")
def monitoring_url(monitored_server: tuple[str, str]) -> str:
    return monitored_server[0]


@pytest.fixture(scope="module")
def monitored_client_url(monitored_server: tuple[str, str]) -> str:
    return monitored_server[1]


@pytest.fixture
async def client(monitoring_url: str):
    async with MonitoringClient(monitoring_url, timeout=5.0) as c:
        yield c


async def test_varz_still_publishes_every_field_the_summary_reads(client) -> None:
    fetched = await client.varz()
    varz = to_varz_summary(fetched.value)

    assert fetched.result.status_code == 200
    assert fetched.result.ok
    assert fetched.result.latency_ms > 0
    assert varz.server_name == "nats-monitor"
    assert varz.version
    assert varz.server_id
    assert varz.start and varz.uptime
    assert varz.cores > 0
    assert varz.max_payload > 0
    assert varz.jetstream_enabled is True
    assert varz.connections >= 0


async def test_connz_honours_sort_limit_and_offset(client) -> None:
    page = to_connz_page((await client.connz(ConnzQuery(sort="cid", limit=1, subs=True))).value)
    assert page.limit == 1
    assert page.offset == 0
    assert page.total >= 0
    assert len(page.connections) <= 1


async def test_connz_rejects_a_sort_the_server_does_not_know(client) -> None:
    """A 400 is still a measured call, and the message names the valid options."""
    with pytest.raises(MonitoringError) as caught:
        await client.connz(ConnzQuery(sort="not-a-column"))
    assert caught.value.result.status_code == 400
    assert caught.value.result.ok is False


async def test_routez_leafz_and_gatewayz_merge_into_one_table(client) -> None:
    routez = (await client.routez()).value
    leafz = (await client.leafz()).value
    gatewayz = (await client.gatewayz()).value
    summary = to_routez_summary(routez, leafz, gatewayz)

    # A single-node server has no edges. That is a real answer, not a missing one.
    assert summary.num_routes == 0
    assert summary.num_leafnodes == 0
    assert summary.num_gateways == 0
    assert summary.routes == ()
    assert summary.now


async def test_jsz_reports_the_api_counters(client) -> None:
    jsz = to_jsz_summary((await client.jsz(streams=True, consumers=True)).value)
    assert jsz.disabled is False
    assert jsz.streams >= 0
    assert jsz.api_total >= 0
    assert jsz.api_errors >= 0


async def test_a_healthy_server_answers_200_on_every_standard_probe(client) -> None:
    checks = [await client.healthz(q) for q in health_battery(HealthQuery())]
    assert [c.status_code for c in checks] == [200, 200, 200]
    assert all(c.ok for c in checks)
    assert all(c.status == "ok" for c in checks)
    assert all(c.label for c in checks)


async def test_a_failing_stream_probe_is_a_row_and_not_an_exception(client) -> None:
    """The design shows a red /healthz row. What matters is that it is a *row*.

    A real server answers stream probes with several different non-200 codes --
    400 when the account is missing, 404 when JetStream does not know the account,
    503 when a consumer is behind. Every one of them has to arrive as a rendered
    result carrying its explanation, because a monitoring screen that throws on
    the interesting answers is useless.
    """
    without_account = await client.healthz(HealthQuery(stream="NO-SUCH-STREAM"))
    assert without_account.status_code == 400
    assert "account" in (without_account.error or "")

    with_account = await client.healthz(HealthQuery(account="APP", stream="NO-SUCH-STREAM"))
    assert with_account.status_code == 404

    for check in (without_account, with_account):
        assert check.ok is False
        assert check.error, "a failed check with no explanation would be useless on screen"
        assert check.path.startswith("/healthz?")


async def test_a_server_without_a_monitoring_port_produces_the_fix_not_a_zero(
    nats_bare: str,
    free_tcp_port: int,
) -> None:
    """The honest empty state, against a port that is genuinely closed.

    `free_tcp_port` rather than the bare container's 8222: that container maps no
    monitoring port at all, so hardcoding 8222 would silently probe whatever else
    happens to be listening on the host running this suite -- which is exactly how
    this test once passed against an unrelated NATS server.
    """
    host = urlsplit(nats_bare).hostname or "127.0.0.1"
    url = f"http://{host}:{free_tcp_port}"

    async with MonitoringClient(url, timeout=2.0) as c:
        with pytest.raises(MonitoringError) as caught:
            await c.varz()

    result = caught.value.result
    assert result.status_code == 0
    assert result.ok is False
    assert caught.value.detail, "an unreachable port with no explanation is not actionable"
    assert "ConnectError" in caught.value.detail or "Errno" in caught.value.detail, (
        f"the failure mode should be named, got {caught.value.detail!r}"
    )

    from datetime import UTC, datetime

    from nats_lens.conn.poller import MonitorSnapshot

    view = MonitorView(
        url,
        MonitorSnapshot(at=datetime.now(UTC), monotonic=0.0, error=caught.value.detail),
    )
    for sourced in (view.varz(), view.traffic(), view.jetstream(), view.rates()):
        assert sourced.value is None
        assert sourced.unavailable is not None
        assert sourced.unavailable.reason is Reason.MONITORING_UNREACHABLE
        # The fix sentence must carry the specific failure, not just the generic
        # remedy -- "connection refused" is what tells you it is a firewall and
        # not a typo.
        assert caught.value.detail in sourced.unavailable.fix

    overview = view.overview()
    assert overview.reachable is False
    assert overview.varz is None and overview.jsz is None and overview.rates is None
    assert overview.varz_rows == ()
    assert overview.error


async def test_two_real_polls_produce_a_sampled_rate(monitoring_url: str) -> None:
    """The `sampled` badge, end to end: two polls of a live server and the gap between."""
    import asyncio
    import uuid

    from nats_lens.conn.poller import MonitorPoller

    poller = MonitorPoller(uuid.uuid4(), monitoring_url, 0.5)
    try:
        await poller.poll_once()
        await asyncio.sleep(0.6)
        await poller.poll_once()

        rates = poller.rates
        assert rates is not None
        assert rates.window_ms >= 500
        assert rates.in_msgs_per_sec >= 0

        view = MonitorView(monitoring_url, poller.latest, 0.5, rates=rates)
        assert view.rates().source is Source.SAMPLED
        assert view.varz().source is Source.MONITOR
    finally:
        await poller.aclose()


async def test_the_interest_graph_answers_who_is_listening(client) -> None:
    """`/subsz` is the only view of another connection's subscriptions.

    The client protocol cannot see them, so without this there is no way to tell
    a subject nobody is listening to from one that is simply quiet -- and a core
    NATS publish to the former is dropped silently.
    """
    from nats_lens.conn.monitoring import to_subsz_summary
    from nats_lens.domain.monitor.schemas import SubszQuery

    fetched = await client.subsz(SubszQuery(limit=50))
    summary = to_subsz_summary(fetched.value)

    assert summary.num_subscriptions > 0, "a running server always holds some of its own"
    assert summary.subscriptions, "subs=true should return the rows themselves"
    assert 0.0 <= summary.cache_hit_rate <= 1.0
    assert all(row.subject for row in summary.subscriptions)


async def test_test_reports_whether_a_subject_reaches_anyone(
    client, monitored_client_url: str
) -> None:
    """The `test` parameter turns the listing into a question."""
    import nats

    from nats_lens.conn.monitoring import to_subsz_summary
    from nats_lens.domain.monitor.schemas import SubszQuery

    # The same server the monitoring client is reading, or the subscription
    # would be invisible to it.
    nc = await nats.connect(monitored_client_url)
    try:
        await nc.subscribe("interest.check")
        await nc.flush()

        listening = to_subsz_summary((await client.subsz(SubszQuery(test="interest.check"))).value)
        assert listening.subscriptions, "a subject with a subscriber must report one"

        silent = to_subsz_summary((await client.subsz(SubszQuery(test="nobody.here"))).value)
        assert not silent.subscriptions, "a subject with no interest must report none"
    finally:
        await nc.close()
