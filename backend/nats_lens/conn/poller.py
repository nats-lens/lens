"""One polling task per server that has a monitoring URL.

Three rules shape this file.

**No time series.** Exactly two samples are kept: the last poll and the one
before it. Rates are the difference between them divided by the elapsed window,
labelled `Source.SAMPLED` and carrying that window, so nobody can mistake them
for a counter the server published. History belongs in
prometheus-nats-exporter, and the Monitor screen says so.

**Do not poll what nobody is looking at.** `/varz`, `/jsz` and `/healthz` are
cheap and feed the header, so they go every tick. `/connz` and `/routez` are not,
so they run only while the Connections or Routes tab is being read. Opening those
tabs registers interest that expires on its own if the user walks away.

**Give up loudly, not silently.** Three consecutive failures open a circuit
breaker: the interval backs off to 30 seconds and every monitor-sourced value
becomes an explicit absence carrying the real errno, rather than the last good
number going quietly stale on screen.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum

import msgspec

from nats_lens.conn.monitoring import (
    MonitoringClient,
    MonitoringError,
    to_connz_page,
    to_jsz_summary,
    to_routez_summary,
    to_subsz_summary,
    to_varz_summary,
)
from nats_lens.domain.monitor.schemas import (
    ConnzPage,
    ConnzQuery,
    EndpointResult,
    HealthCheck,
    HealthQuery,
    JszSummary,
    RateSample,
    RoutezSummary,
    SubszQuery,
    SubszSummary,
    VarzSummary,
)

BREAKER_THRESHOLD = 3
"""Consecutive failed ticks before the poller stops trying at the normal rate."""

BREAKER_BACKOFF_SECONDS = 30.0
"""Where the interval goes once the breaker is open."""

WATCH_TTL_SECONDS = 30.0
"""How long a tab stays interesting after the last request for it."""


class Tab(StrEnum):
    """The tabs whose endpoints are expensive enough to fetch only on demand."""

    CONNECTIONS = "connections"
    ROUTES = "routes"
    SUBSCRIPTIONS = "subscriptions"


HEALTH_BATTERY: tuple[HealthQuery, ...] = (
    HealthQuery(),
    HealthQuery(js_enabled_only=True),
    HealthQuery(js_server_only=True),
)
"""The three probes the design shows unconditionally. Each is a separate request."""


class MonitorSnapshot(msgspec.Struct, frozen=True):
    """Everything one tick saw, including what it failed to see."""

    at: datetime
    monotonic: float
    varz: VarzSummary | None = None
    jsz: JszSummary | None = None
    connz: ConnzPage | None = None
    routez: RoutezSummary | None = None
    health: tuple[HealthCheck, ...] = ()
    endpoints: tuple[EndpointResult, ...] = ()
    error: str | None = None
    """The failure detail, errno included. Appended to the fix the user is shown."""

    @property
    def reachable(self) -> bool:
        return self.varz is not None

    @property
    def status_code(self) -> int | None:
        """The status of the call the header shows next to the URL, which is /varz."""
        for endpoint in self.endpoints:
            if endpoint.path.startswith("/varz"):
                return endpoint.status_code
        return None

    @property
    def latency_ms(self) -> float | None:
        for endpoint in self.endpoints:
            if endpoint.path.startswith("/varz"):
                return endpoint.latency_ms
        return None


def rates_between(
    previous: VarzSummary, latest: VarzSummary, window_ms: float
) -> RateSample | None:
    """Per-second figures from two counter readings, or nothing.

    Returns None rather than a guess in the two cases where a difference would be
    a lie: a window too short to divide by, and a restarted server, whose counters
    began again from zero. `start` changing, or any delta going negative, is that
    restart -- and showing the resulting spike as throughput would be worse than
    showing nothing until the next poll.
    """
    if window_ms <= 0:
        return None
    if previous.start and latest.start and previous.start != latest.start:
        return None

    deltas = (
        latest.in_msgs - previous.in_msgs,
        latest.out_msgs - previous.out_msgs,
        latest.in_bytes - previous.in_bytes,
        latest.out_bytes - previous.out_bytes,
    )
    if any(d < 0 for d in deltas):
        return None

    seconds = window_ms / 1000.0
    in_msgs, out_msgs, in_bytes, out_bytes = deltas
    return RateSample(
        in_msgs_per_sec=round(in_msgs / seconds, 3),
        out_msgs_per_sec=round(out_msgs / seconds, 3),
        in_bytes_per_sec=round(in_bytes / seconds, 3),
        out_bytes_per_sec=round(out_bytes / seconds, 3),
        window_ms=round(window_ms),
    )


class MonitorPoller:
    """The polling loop for one server, and the two samples it keeps."""

    def __init__(
        self,
        server_id: uuid.UUID,
        base_url: str,
        poll_seconds: float = 5.0,
        *,
        timeout: float = 5.0,
        client: MonitoringClient | None = None,
    ) -> None:
        self.server_id = server_id
        self.base_url = base_url
        self.poll_seconds = max(poll_seconds, 0.5)
        self._timeout = timeout
        self._client = client or MonitoringClient(base_url, timeout=timeout)
        self._owns_client = client is None

        self._latest: MonitorSnapshot | None = None
        self._good: tuple[MonitorSnapshot, ...] = ()
        """At most two: (previous, last). The whole time series nats-lens keeps."""

        self._failures = 0
        self._interest: dict[str, float] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._closing: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()

    # -------------------------------------------------------------- what it saw

    @property
    def latest(self) -> MonitorSnapshot | None:
        """The most recent tick, whether or not it succeeded."""
        return self._latest

    @property
    def last(self) -> MonitorSnapshot | None:
        return self._good[-1] if self._good else None

    @property
    def previous(self) -> MonitorSnapshot | None:
        return self._good[0] if len(self._good) == 2 else None

    @property
    def rates(self) -> RateSample | None:
        last, previous = self.last, self.previous
        if last is None or previous is None or last.varz is None or previous.varz is None:
            return None
        return rates_between(
            previous.varz, last.varz, (last.monotonic - previous.monotonic) * 1000.0
        )

    @property
    def failures(self) -> int:
        return self._failures

    @property
    def breaker_open(self) -> bool:
        return self._failures >= BREAKER_THRESHOLD

    @property
    def interval(self) -> float:
        return BREAKER_BACKOFF_SECONDS if self.breaker_open else self.poll_seconds

    # -------------------------------------------------------------- tab interest

    def note_interest(self, tab: Tab | str) -> None:
        """Record that someone is reading a tab, so the loop starts polling it."""
        self._interest[str(tab)] = _now() + WATCH_TTL_SECONDS

    def is_watching(self, tab: Tab | str) -> bool:
        return self._interest.get(str(tab), 0.0) > _now()

    # -------------------------------------------------------------- the loop

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=f"monitor-poll-{self.server_id}")

    def reconfigure(self, base_url: str, poll_seconds: float) -> None:
        """Apply a settings change now rather than after the current sleep.

        The interval is the visible half of this: a user who drops the poll from
        30 seconds to 1 expects the screen to speed up immediately, not once the
        30-second sleep they were already in finally returns. Hence the event.
        """
        poll_seconds = max(poll_seconds, 0.5)
        url_changed = base_url.rstrip("/") != self.base_url.rstrip("/")
        if url_changed:
            old = self._client
            self.base_url = base_url
            self._client = MonitoringClient(base_url, timeout=self._timeout)
            self._owns_client = True
            # Samples from the old URL describe a different server. Keeping them
            # would let a rate be computed across the change.
            self._reset()
            closing = asyncio.create_task(_close_quietly(old))
            self._closing.add(closing)
            closing.add_done_callback(self._closing.discard)
        self.poll_seconds = poll_seconds
        self._wake.set()

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._owns_client:
            await _close_quietly(self._client)

    async def _run(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                await self.poll_once()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.interval)
            self._wake.clear()

    async def ensure_sample(self) -> MonitorSnapshot:
        """A snapshot for the first request, which arrives before the first tick."""
        if self._latest is not None:
            return self._latest
        return await self.poll_once()

    # -------------------------------------------------------------- one tick

    async def poll_once(self) -> MonitorSnapshot:
        """Read the endpoints this tick needs and fold them into a snapshot.

        Sequential on purpose. The requests are small and the interval is seconds,
        so there is nothing to gain from firing five at once at a server that is
        already busy doing its real job.
        """
        async with self._lock:
            endpoints: list[EndpointResult] = []
            varz: VarzSummary | None = None
            jsz: JszSummary | None = None
            connz: ConnzPage | None = None
            routez: RoutezSummary | None = None
            health: list[HealthCheck] = []
            error: str | None = None

            try:
                fetched = await self._client.varz()
            except MonitoringError as exc:
                endpoints.append(exc.result)
                error = exc.detail
            else:
                endpoints.append(fetched.result)
                varz = to_varz_summary(fetched.value)

            if varz is not None:
                try:
                    fetched_jsz = await self._client.jsz()
                except MonitoringError as exc:
                    endpoints.append(exc.result)
                else:
                    endpoints.append(fetched_jsz.result)
                    jsz = to_jsz_summary(fetched_jsz.value)

                for query in HEALTH_BATTERY:
                    try:
                        check = await self._client.healthz(query)
                    except MonitoringError as exc:
                        endpoints.append(exc.result)
                    else:
                        health.append(check)

                if self.is_watching(Tab.CONNECTIONS):
                    try:
                        fetched_connz = await self._client.connz(ConnzQuery())
                    except MonitoringError as exc:
                        endpoints.append(exc.result)
                    else:
                        endpoints.append(fetched_connz.result)
                        connz = to_connz_page(fetched_connz.value)

                if self.is_watching(Tab.ROUTES):
                    routez, route_results = await self._routes()
                    endpoints.extend(route_results)

            snapshot = MonitorSnapshot(
                at=datetime.now(UTC),
                monotonic=_now(),
                varz=varz,
                jsz=jsz,
                connz=connz,
                routez=routez,
                health=tuple(health),
                endpoints=tuple(endpoints),
                error=error,
            )
            self._record(snapshot)
            return snapshot

    async def _routes(self) -> tuple[RoutezSummary | None, list[EndpointResult]]:
        """`/routez` plus the two endpoints that complete the cluster picture.

        A missing `/leafz` or `/gatewayz` is not a failure: older servers do not
        serve them, and the routes table is still worth showing without them.
        """
        results: list[EndpointResult] = []
        try:
            fetched = await self._client.routez()
        except MonitoringError as exc:
            results.append(exc.result)
            return None, results
        results.append(fetched.result)

        leafz = gatewayz = None
        try:
            fetched_leafz = await self._client.leafz()
        except MonitoringError as exc:
            results.append(exc.result)
        else:
            results.append(fetched_leafz.result)
            leafz = fetched_leafz.value

        try:
            fetched_gatewayz = await self._client.gatewayz()
        except MonitoringError as exc:
            results.append(exc.result)
        else:
            results.append(fetched_gatewayz.result)
            gatewayz = fetched_gatewayz.value

        return to_routez_summary(fetched.value, leafz, gatewayz), results

    def _record(self, snapshot: MonitorSnapshot) -> None:
        self._latest = snapshot
        if snapshot.reachable:
            self._failures = 0
            self._good = (*self._good, snapshot)[-2:]
        else:
            self._failures += 1

    def _reset(self) -> None:
        self._latest = None
        self._good = ()
        self._failures = 0

    # -------------------------------------------------------------- on demand

    async def fetch_connections(self, query: ConnzQuery) -> ConnzPage:
        """`/connz` with the caller's own sort, limit, offset and flags.

        Fetched rather than served from the snapshot because the query surface is
        the point: two users sorting by different columns are asking the server two
        different questions, and a cached answer to one is not an answer to the other.
        """
        self.note_interest(Tab.CONNECTIONS)
        fetched = await self._client.connz(query)
        return to_connz_page(fetched.value)

    async def fetch_subscriptions(self, query: SubszQuery) -> SubszSummary:
        """`/subsz`. Always fetched, never cached: `test` makes each call a
        different question, and the interest graph changes as clients come and go."""
        self.note_interest(Tab.SUBSCRIPTIONS)
        fetched = await self._client.subsz(query)
        return to_subsz_summary(fetched.value)

    async def fetch_routes(self) -> RoutezSummary:
        self.note_interest(Tab.ROUTES)
        summary, _ = await self._routes()
        if summary is None:
            raise MonitoringError(
                EndpointResult(
                    path="/routez",
                    status_code=0,
                    latency_ms=0.0,
                    ok=False,
                    description="cluster routes",
                    error="The monitoring port did not answer /routez.",
                )
            )
        return summary

    async def fetch_health(self, queries: Iterable[HealthQuery]) -> list[HealthCheck]:
        """Each probe is its own request, because each asks a different question."""
        return [await self._client.healthz(query) for query in queries]


async def _close_quietly(client: MonitoringClient) -> None:
    with contextlib.suppress(Exception):
        await client.aclose()


def _now() -> float:
    return asyncio.get_running_loop().time()


class MonitorPollers:
    """Every live poller, keyed by server.

    Process-global by design: nats-lens refuses to start with more than one worker
    precisely because this state, the NATS connections and the capture rings all
    live in memory, and a second worker would poll a second time and serve a
    different answer to the same question.
    """

    def __init__(self) -> None:
        self._pollers: dict[uuid.UUID, MonitorPoller] = {}

    def get(self, server_id: uuid.UUID) -> MonitorPoller | None:
        return self._pollers.get(server_id)

    def ensure(
        self,
        server_id: uuid.UUID,
        base_url: str,
        poll_seconds: float,
        *,
        timeout: float = 5.0,
    ) -> MonitorPoller:
        poller = self._pollers.get(server_id)
        if poller is None:
            poller = MonitorPoller(server_id, base_url, poll_seconds, timeout=timeout)
            self._pollers[server_id] = poller
        else:
            poller.reconfigure(base_url, poll_seconds)
        poller.start()
        return poller

    def put(self, poller: MonitorPoller) -> MonitorPoller:
        """Register a poller built elsewhere. Used by tests to inject a transport."""
        self._pollers[poller.server_id] = poller
        return poller

    async def drop(self, server_id: uuid.UUID) -> None:
        poller = self._pollers.pop(server_id, None)
        if poller is not None:
            await poller.aclose()

    async def aclose(self) -> None:
        pollers, self._pollers = self._pollers, {}
        for poller in pollers.values():
            await poller.aclose()


_POLLERS = MonitorPollers()


def pollers() -> MonitorPollers:
    return _POLLERS
