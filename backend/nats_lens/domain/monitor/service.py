"""What the Monitor screen shows, and what it says when it can see nothing.

The rule this file exists to enforce: a server with no monitoring URL, or with a
monitoring port that does not answer, produces named absences and never a zero.
`MonitorView` is the whole of that rule, and it is a pure function of one
snapshot -- no database, no sockets -- so it can be tested against the case that
matters without either.

`MonitorService` is the thin part: load the server row, make sure its poller is
running, and hand the snapshot to the view.
"""

from __future__ import annotations

import functools
import re
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

from litestar.exceptions import NotFoundException, ServiceUnavailableException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nats_lens.config import Settings
from nats_lens.conn.monitoring import MonitoringError
from nats_lens.conn.poller import MonitorPoller, MonitorSnapshot, pollers
from nats_lens.db.models import Server
from nats_lens.domain.common import KeyValueRow
from nats_lens.domain.monitor.schemas import (
    ConnzPage,
    ConnzQuery,
    HealthCheck,
    HealthQuery,
    JszSummary,
    MonitorOverview,
    PrometheusHint,
    RateSample,
    RoutezSummary,
    SubszQuery,
    SubszSummary,
    VarzSummary,
)
from nats_lens.domain.servers.schemas import TrafficFacts
from nats_lens.provenance import Reason, Source, Sourced

EXPORTER_IMAGE = "natsio/prometheus-nats-exporter:0.17.3"
EXPORTER_PORT = 7777
GRAFANA_DASHBOARD = "https://grafana.com/grafana/dashboards/2279-nats-server-dashboard/"
SURVEYOR_NOTE = (
    "nats-surveyor reads the same counters over the $SYS account rather than each "
    "server's monitoring port, which is the easier fit once there are more servers "
    "than there are ports you want to expose."
)


@functools.cache
def _settings() -> Settings:
    return Settings.from_env()


# ------------------------------------------------------------------ the view


class MonitorView:
    """One server's monitoring state, and the provenance of every figure in it.

    Deliberately holds no connection and no session. Everything it needs is the
    monitoring URL (or its absence) and the last snapshot (or its absence), which
    is exactly the pair the honesty rule turns on.
    """

    def __init__(
        self,
        monitoring_url: str | None,
        snapshot: MonitorSnapshot | None,
        poll_seconds: float = 5.0,
        *,
        rates: RateSample | None = None,
    ) -> None:
        self.monitoring_url = (monitoring_url or "").strip()
        self.snapshot = snapshot
        self.poll_seconds = poll_seconds
        self._rates = rates

    @property
    def configured(self) -> bool:
        return bool(self.monitoring_url)

    @property
    def reachable(self) -> bool:
        return self.snapshot is not None and self.snapshot.reachable

    @property
    def reason(self) -> Reason | None:
        """Why there is nothing to show, or None when there is."""
        if not self.configured:
            return Reason.MONITORING_NOT_CONFIGURED
        if not self.reachable:
            return Reason.MONITORING_UNREACHABLE
        return None

    @property
    def detail(self) -> str | None:
        """The specific error appended to the fix. The errno lives here."""
        if not self.configured:
            return None
        if self.snapshot is None:
            return "The monitoring port has not answered since nats-lens started."
        return self.snapshot.error

    # ---------------------------------------------------------- sourced values

    def varz(self) -> Sourced[VarzSummary]:
        if self.reason is not None or self.snapshot is None or self.snapshot.varz is None:
            return Sourced.missing(
                Source.MONITOR, self.reason or Reason.MONITORING_UNREACHABLE, self.detail
            )
        return Sourced.known(self.snapshot.varz, Source.MONITOR)

    def jetstream(self) -> Sourced[JszSummary]:
        if self.reason is not None or self.snapshot is None:
            return Sourced.missing(
                Source.MONITOR, self.reason or Reason.MONITORING_UNREACHABLE, self.detail
            )
        if self.snapshot.jsz is None:
            return Sourced.missing(
                Source.MONITOR,
                Reason.MONITORING_UNREACHABLE,
                "The monitoring port answered but /jsz did not.",
            )
        if self.snapshot.jsz.disabled:
            return Sourced.missing(Source.MONITOR, Reason.JETSTREAM_NOT_ENABLED)
        return Sourced.known(self.snapshot.jsz, Source.MONITOR)

    def traffic(self) -> Sourced[TrafficFacts]:
        """The Servers screen's throughput card. The one figure a client cannot see."""
        varz = self.varz()
        if varz.value is None:
            return Sourced(value=None, source=varz.source, at=varz.at, unavailable=varz.unavailable)
        v = varz.value
        return Sourced.known(
            TrafficFacts(
                in_msgs=v.in_msgs,
                out_msgs=v.out_msgs,
                in_bytes=v.in_bytes,
                out_bytes=v.out_bytes,
                connections=v.connections,
                subscriptions=v.subscriptions,
                slow_consumers=v.slow_consumers,
                routes=v.routes,
            ),
            Source.MONITOR,
        )

    def rates(self) -> Sourced[RateSample]:
        """Sampled, not published.

        Tagged `SAMPLED` rather than `MONITOR` even though the counters behind it
        came over the monitoring port, because the per-second figure is nats-lens's
        own arithmetic over two polls and no server ever reported it.
        """
        if self.reason is not None:
            return Sourced.missing(Source.SAMPLED, self.reason, self.detail)
        if self._rates is None:
            return Sourced.missing(
                Source.SAMPLED,
                Reason.MONITORING_UNREACHABLE,
                "Rates need two polls. The second one has not happened yet.",
            )
        return Sourced.known(self._rates, Source.SAMPLED)

    # ---------------------------------------------------------- the screen

    def overview(self) -> MonitorOverview:
        """The Server tab. Absent everywhere the port could not be read."""
        snapshot = self.snapshot
        if self.reason is not None or snapshot is None or snapshot.varz is None:
            return MonitorOverview(
                url=self.monitoring_url,
                reachable=False,
                status_code=snapshot.status_code if snapshot else None,
                latency_ms=snapshot.latency_ms if snapshot else None,
                poll_seconds=self.poll_seconds,
                varz=None,
                rates=None,
                jsz=None,
                varz_rows=(),
                error=self.unavailable_error(),
            )

        jsz = snapshot.jsz
        return MonitorOverview(
            url=self.monitoring_url,
            reachable=True,
            status_code=snapshot.status_code,
            latency_ms=snapshot.latency_ms,
            poll_seconds=self.poll_seconds,
            varz=snapshot.varz,
            rates=self._rates,
            jsz=None if jsz is None or jsz.disabled else jsz,
            varz_rows=varz_rows(snapshot.varz),
            error=None,
        )

    def unavailable_error(self) -> str:
        """The fix sentence, for the endpoints whose contract has nowhere to put it."""
        reason = self.reason or Reason.MONITORING_UNREACHABLE
        sourced: Sourced[object] = Sourced.missing(Source.MONITOR, reason, self.detail)
        return sourced.unavailable.fix if sourced.unavailable else ""


# ------------------------------------------------------------------ formatting


def varz_rows(varz: VarzSummary) -> tuple[KeyValueRow, ...]:
    """The design's stat cards, in the design's order, formatted for reading.

    Grouped counts first because they answer 'is this server healthy', then the
    throughput totals, then the resources. Nothing here is derived or estimated --
    every figure is a number `/varz` published.
    """
    return (
        KeyValueRow(k="Uptime", v=spaced_uptime(varz.uptime)),
        KeyValueRow(k="Connections", v=f"{varz.connections:,}"),
        KeyValueRow(k="Total connections", v=compact(varz.total_connections)),
        KeyValueRow(k="Slow consumers", v=f"{varz.slow_consumers:,}"),
        KeyValueRow(k="Messages in", v=compact(varz.in_msgs)),
        KeyValueRow(k="Messages out", v=compact(varz.out_msgs)),
        KeyValueRow(k="Bytes in", v=human_bytes(varz.in_bytes)),
        KeyValueRow(k="Memory", v=human_bytes(varz.mem)),
        KeyValueRow(k="Subscriptions", v=f"{varz.subscriptions:,}"),
        KeyValueRow(k="Routes", v=f"{varz.routes:,}"),
        KeyValueRow(k="Leaf nodes", v=f"{varz.leafnodes:,}"),
        KeyValueRow(k="CPU", v=f"{varz.cpu:.0f}% of {varz.cores} cores"),
    )


def compact(n: int) -> str:
    """4120338 -> 4.1M. Long counters are unreadable in a card at full length."""
    for limit, suffix in ((1_000_000_000_000, "T"), (1_000_000_000, "B"), (1_000_000, "M")):
        if abs(n) >= limit:
            return f"{n / limit:.1f}{suffix}"
    return f"{n:,}"


def human_bytes(n: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    size = float(n)
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


_UPTIME_PART = re.compile(r"(\d+[a-z]+)")


def spaced_uptime(uptime: str) -> str:
    """`21d4h12m8s` as nats-server writes it, spaced out as the design reads it."""
    parts = _UPTIME_PART.findall(uptime)
    return " ".join(parts) if parts else uptime


def health_battery(query: HealthQuery) -> list[HealthQuery]:
    """Which probes to run for one request.

    An explicit `js-enabled-only` or `js-server-only` is a single question and gets
    a single row. Anything else gets the standard three, plus the stream and
    consumer checks the caller named, because those are the ones that turn a green
    header into the 503 the design shows.
    """
    if query.js_enabled_only or query.js_server_only:
        return [query]

    battery = [
        HealthQuery(account=query.account),
        HealthQuery(js_enabled_only=True),
        HealthQuery(js_server_only=True),
    ]
    if query.stream:
        battery.append(HealthQuery(stream=query.stream, account=query.account))
        if query.consumer:
            battery.append(
                HealthQuery(stream=query.stream, consumer=query.consumer, account=query.account)
            )
    return battery


def prometheus_hint(monitoring_url: str | None) -> PrometheusHint:
    """Where history lives, since nats-lens keeps none.

    Answered even for a server with no monitoring URL: it is advice about what to
    run, not a reading from a port, and a user who has neither needs it most.
    """
    target = (monitoring_url or "").strip() or "http://your-nats-host:8222"
    host = urlsplit(target).hostname or "your-exporter-host"
    return PrometheusHint(
        exporter_image=EXPORTER_IMAGE,
        exporter_command=(
            f"prometheus-nats-exporter -varz -connz -routez -subz -gatewayz -leafz "
            f"-jsz=all -port {EXPORTER_PORT} {target}"
        ),
        scrape_url=f"http://{host}:{EXPORTER_PORT}/metrics",
        surveyor_note=SURVEYOR_NOTE,
        grafana_dashboard_url=GRAFANA_DASHBOARD,
    )


# ------------------------------------------------------------------ the service


class MonitorService:
    """Loads the server, keeps its poller alive, and answers the Monitor routes."""

    def __init__(self, session: AsyncSession, *, timeout: float | None = None) -> None:
        self._session = session
        self._timeout = timeout if timeout is not None else _settings().monitor_timeout_seconds

    async def _server(self, server_id: uuid.UUID) -> Server:
        server = (
            await self._session.execute(select(Server).where(Server.id == server_id))
        ).scalar_one_or_none()
        if server is None:
            raise NotFoundException(detail=f"No server is registered with id {server_id}.")
        return server

    async def _poller(self, server: Server) -> MonitorPoller | None:
        """The running poller for a server, or None when it has no monitoring URL."""
        url = (server.monitoring_url or "").strip()
        if not url:
            return None
        return pollers().ensure(
            server.id, url, server.monitoring_poll_seconds, timeout=self._timeout
        )

    async def view(self, server_id: uuid.UUID) -> MonitorView:
        server = await self._server(server_id)
        poller = await self._poller(server)
        snapshot = await poller.ensure_sample() if poller is not None else None
        return MonitorView(
            server.monitoring_url,
            snapshot,
            server.monitoring_poll_seconds,
            rates=poller.rates if poller is not None else None,
        )

    async def overview(self, server_id: uuid.UUID) -> MonitorOverview:
        return (await self.view(server_id)).overview()

    async def traffic(self, server_id: uuid.UUID) -> Sourced[TrafficFacts]:
        """For the Servers screen, which needs the same figures with their badge."""
        return (await self.view(server_id)).traffic()

    async def connections(self, server_id: uuid.UUID, query: ConnzQuery) -> ConnzPage:
        server = await self._server(server_id)
        poller = await self._poller(server)
        if poller is None:
            raise self._unavailable(server, None)
        try:
            return await poller.fetch_connections(query)
        except MonitoringError as exc:
            raise self._unavailable(server, exc.detail) from exc

    async def subscriptions(self, server_id: uuid.UUID, query: SubszQuery) -> SubszSummary:
        """Who is listening, and whether a given subject reaches anyone."""
        server = await self._server(server_id)
        poller = await self._poller(server)
        if poller is None:
            raise self._unavailable(server, None)
        try:
            return await poller.fetch_subscriptions(query)
        except MonitoringError as exc:
            raise self._unavailable(server, exc.detail) from exc

    async def routes(self, server_id: uuid.UUID) -> RoutezSummary:
        server = await self._server(server_id)
        poller = await self._poller(server)
        if poller is None:
            raise self._unavailable(server, None)
        try:
            return await poller.fetch_routes()
        except MonitoringError as exc:
            raise self._unavailable(server, exc.detail) from exc

    async def health(self, server_id: uuid.UUID, query: HealthQuery) -> list[HealthCheck]:
        server = await self._server(server_id)
        poller = await self._poller(server)
        if poller is None:
            raise self._unavailable(server, None)
        try:
            return await poller.fetch_health(health_battery(query))
        except MonitoringError as exc:
            raise self._unavailable(server, exc.detail) from exc

    async def prometheus(self, server_id: uuid.UUID) -> PrometheusHint:
        server = await self._server(server_id)
        return prometheus_hint(server.monitoring_url)

    @staticmethod
    def _unavailable(server: Server, detail: str | None) -> ServiceUnavailableException:
        """503 carrying the same fix sentence a `Sourced` absence would have.

        `ConnzPage` and `RoutezSummary` are whole responses read from the
        monitoring port; there is no field in them for an explanation, so the
        explanation becomes the status. The one thing that must not happen is a
        body of zeros that reads like an idle server.
        """
        snapshot = (
            MonitorSnapshot(at=datetime.now(UTC), monotonic=0.0, error=detail)
            if detail is not None
            else None
        )
        view = MonitorView(server.monitoring_url, snapshot)
        return ServiceUnavailableException(detail=view.unavailable_error())
