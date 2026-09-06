"""The Servers screen, assembled.

The interesting work here is not CRUD, it is deciding what a panel is allowed to
say. `TelemetrySources` is the whole of it: which of `monitor` and `system` a server
can actually serve decides whether the traffic card shows numbers or names the fix,
and the design draws four different screens from that one answer.

Reachability is what counts, not configuration. A monitoring URL that is set but
firewalled off is worth exactly as much as no monitoring URL at all, so it is
probed rather than trusted.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

from litestar.di import NamedDependency
from litestar.exceptions import NotFoundException
from sqlalchemy.ext.asyncio import AsyncSession

from nats_lens.conn.auth import AUTH_LABELS, AuthError, AuthSpec, TlsSpec, connect_kwargs
from nats_lens.conn.connection import (
    ManagedConnection,
    MonitoringHealth,
    not_configured,
    traffic_unavailable,
)
from nats_lens.conn.errors import NatsProblem, classify, describe
from nats_lens.conn.manager import ConnectionManager
from nats_lens.crypto import SecretKind
from nats_lens.db.models import Server
from nats_lens.domain.common import KeyValueRow
from nats_lens.domain.monitor.service import MonitorService
from nats_lens.domain.servers.repository import SecretVault, ServerRepository, to_config
from nats_lens.domain.servers.schemas import (
    AuthMode,
    ClientFacts,
    ConnectionState,
    JetStreamAccountFacts,
    ProbeRequest,
    ProbeResponse,
    ProbeResult,
    ProbeTarget,
    ServerConfig,
    ServerCreate,
    ServerDetail,
    ServerSummary,
    ServerUpdate,
    TelemetrySource,
    TelemetrySources,
    TrafficFacts,
    derive_monitoring_url,
)
from nats_lens.provenance import Reason, Source, Sourced

MONITORING_LABEL = "Monitoring endpoint"
SYSTEM_LABEL = "System account"

NOTE_BOTH = (
    "Connections, subscriptions, slow consumers and route health are read from the "
    "monitoring port, and refreshed by $SYS.SERVER.*.STATSZ heartbeats between polls."
)
NOTE_MONITORING = (
    "Counters are polled from the monitoring port every {poll} seconds. Without a system "
    "account there are no push events, so connect and disconnect show up on the next poll "
    "rather than instantly."
)
NOTE_SYSTEM = (
    "{why}, so counters come from STATSZ heartbeats on the system account instead. "
    "Per-connection detail is not reachable that way."
)
NOTE_NONE = (
    "Server-wide counters, including connections, subscriptions, slow consumers and "
    "throughput, are not part of the client protocol. Start the server with http_port: 8222, "
    "or give nats-lens a $SYS user, and this panel fills in. Everything else on this screen "
    "keeps working without them."
)

_PROBE_BODIES: tuple[tuple[str, str], ...] = (
    (
        "NoServersError",
        "None of the URLs answered. Check the address and the port, and that this host "
        "can reach the server at all.",
    ),
    (
        "AuthorizationError",
        "The server answered and then rejected the credentials. Check the authentication "
        "mode and what is stored with it.",
    ),
    (
        "InvalidUserCredentialsError",
        "The credentials could not be read. A .creds file has to carry both the user JWT "
        "and its NKey seed.",
    ),
    (
        "SecureConnRequiredError",
        "The server requires TLS. Turn TLS on, or use a tls:// URL.",
    ),
    (
        "SecureConnFailedError",
        "The TLS handshake failed. Check the CA certificate, and the hostname the "
        "certificate was issued for.",
    ),
    ("TimeoutError", "The server did not finish the handshake in time."),
)
_PROBE_FALLBACK = "The connection attempt failed before it completed."


# ------------------------------------------------------------------ formatting


_UNITS = ("B", "kB", "MB", "GB", "TB", "PB")


def format_bytes(value: int) -> str:
    """Binary units, and no trailing `.0`, so a max payload reads as `1 MB`.

    A negative limit is JetStream's way of saying there isn't one.
    """
    if value < 0:
        return "unlimited"
    size = float(value)
    index = 0
    while size >= 1024 and index < len(_UNITS) - 1:
        size /= 1024
        index += 1
    if index == 0:
        return f"{int(size)} B"
    return f"{size:.1f}".removesuffix(".0") + f" {_UNITS[index]}"


def format_count(value: int) -> str:
    """`84.2M`, `210K`, `12`."""
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(value) >= limit:
            return f"{value / limit:.1f}".removesuffix(".0") + suffix
    return str(value)


def format_ms(value: float) -> str:
    return f"{value:.1f} ms".replace(".0 ms", " ms")


def _poll_text(seconds: float) -> str:
    return f"{seconds:g}"


def _used_of(used: int, limit: int) -> str:
    if limit > 0:
        return f"{format_bytes(used)} of {format_bytes(limit)}"
    return f"{format_bytes(used)}, no limit"


# ----------------------------------------------------------------- telemetry


def telemetry_sources(
    *,
    monitoring: MonitoringHealth,
    poll_seconds: float,
    system_enabled: bool,
    system_connected: bool,
    system_error: str | None,
) -> TelemetrySources:
    """The four states the Servers screen draws, from what is actually reachable."""
    if not monitoring.configured:
        monitoring_row = TelemetrySource(
            label=MONITORING_LABEL, configured=False, reachable=None, detail="not configured"
        )
    else:
        monitoring_row = TelemetrySource(
            label=MONITORING_LABEL,
            configured=True,
            reachable=monitoring.reachable,
            detail=monitoring.detail,
        )

    if not system_enabled:
        system_row = TelemetrySource(
            label=SYSTEM_LABEL, configured=False, reachable=None, detail="not configured"
        )
    elif system_connected:
        system_row = TelemetrySource(
            label=SYSTEM_LABEL,
            configured=True,
            reachable=True,
            detail="$SYS, STATSZ heartbeats",
        )
    else:
        system_row = TelemetrySource(
            label=SYSTEM_LABEL,
            configured=True,
            reachable=False,
            detail=system_error or "not connected",
        )

    mon_ok = monitoring_row.reachable is True
    sys_ok = system_row.reachable is True
    if mon_ok and sys_ok:
        tag, note = "both", NOTE_BOTH
    elif mon_ok:
        tag, note = "monitoring", NOTE_MONITORING.format(poll=_poll_text(poll_seconds))
    elif sys_ok:
        why = (
            "The monitoring port is not answering from here"
            if monitoring_row.configured
            else "No monitoring URL is set"
        )
        tag, note = "system", NOTE_SYSTEM.format(why=why)
    else:
        tag, note = "none", NOTE_NONE
    return TelemetrySources(
        monitoring=monitoring_row, system_account=system_row, tag=tag, note=note
    )


def summary_note(
    *, auth_mode: AuthMode, url_count: int, client: Sourced[ClientFacts], nodes: int
) -> str:
    """The line under a server's name: topology first, then how we authenticate."""
    parts: list[str] = []
    facts = client.value
    if facts is not None:
        if facts.cluster:
            parts.append(f"cluster {facts.cluster}")
        parts.append("single node" if nodes <= 1 else f"{nodes} nodes")
    else:
        parts.append("1 URL" if url_count == 1 else f"{url_count} URLs")
    parts.append(AUTH_LABELS[auth_mode])
    return " · ".join(parts)


def connection_rows(client: Sourced[ClientFacts]) -> tuple[KeyValueRow, ...]:
    """Empty when there is nothing to describe. The card shows the empty state instead."""
    facts = client.value
    if facts is None:
        return ()
    return (
        KeyValueRow(k="Server version", v=facts.version or "unknown"),
        KeyValueRow(k="Cluster", v=facts.cluster or "none"),
        KeyValueRow(k="Round-trip", v=format_ms(facts.rtt_ms)),
        KeyValueRow(k="Max payload", v=format_bytes(facts.max_payload)),
    )


def jetstream_rows(account: Sourced[JetStreamAccountFacts]) -> tuple[KeyValueRow, ...]:
    facts = account.value
    if facts is None:
        return ()
    return (
        KeyValueRow(
            k="Streams / consumers",
            v=f"{format_count(facts.streams)} · {format_count(facts.consumers)}",
        ),
        KeyValueRow(k="Storage used", v=_used_of(facts.storage_used, facts.storage_limit)),
        KeyValueRow(k="Memory used", v=_used_of(facts.memory_used, facts.memory_limit)),
        KeyValueRow(
            k="API requests / errors",
            v=f"{format_count(facts.api_total)} · {format_count(facts.api_errors)}",
        ),
    )


# ------------------------------------------------------------------- service


class ServerService:
    """Everything behind `/api/servers`."""

    def __init__(
        self,
        repo: ServerRepository,
        connections: ConnectionManager,
        session: AsyncSession,
    ) -> None:
        self._repo = repo
        self._conn = connections
        self._session = session
        self._settings = connections.settings
        self._monitor_probe = connections.monitor_probe

    # -------------------------------------------------------------- reading

    async def list_summaries(self) -> list[ServerSummary]:
        rows = await self._repo.list_all()
        return list(await asyncio.gather(*(self._summary(row) for row in rows)))

    async def detail(self, server_id: uuid.UUID) -> ServerDetail:
        return await self._detail(await self._require(server_id))

    async def config(self, server_id: uuid.UUID) -> ServerConfig:
        return to_config(await self._require(server_id))

    async def _require(self, server_id: uuid.UUID) -> Server:
        row = await self._repo.get(server_id)
        if row is None:
            raise NotFoundException(detail=f"No server is registered with id {server_id}.")
        return row

    async def _health(self, row: Server, conn: ManagedConnection | None) -> MonitoringHealth:
        if row.monitoring_url is None:
            return not_configured()
        if conn is not None:
            return await conn.monitoring_health()
        # No connection has ever been opened for this server, but the monitoring port
        # is HTTP and does not need one, so it is still a fair question to ask.
        return await self._monitor_probe(row.monitoring_url, self._settings.monitor_timeout_seconds)

    async def _sources(self, row: Server, conn: ManagedConnection | None) -> TelemetrySources:
        return telemetry_sources(
            monitoring=await self._health(row, conn),
            poll_seconds=row.monitoring_poll_seconds,
            system_enabled=row.system_account_enabled,
            system_connected=conn is not None and conn.system_connected,
            system_error=conn.system_error if conn is not None else None,
        )

    async def _traffic(self, row: Server, conn: ManagedConnection | None) -> Sourced[TrafficFacts]:
        """Throughput and connection counts, which only the monitoring port has.

        Read through `MonitorService`, which owns the poller, rather than from the
        NATS connection: the client protocol cannot see any of these figures, so
        asking the connection for them can only ever produce the unavailable
        envelope. This is the one place the Servers screen and the Monitor screen
        must agree, and they agree by being the same code.
        """
        if not (row.monitoring_url or "").strip():
            return traffic_unavailable(not_configured())
        try:
            return await MonitorService(self._session).traffic(row.id)
        except Exception:
            # The poller reports its own failures inside the envelope; anything
            # thrown past it means we could not even ask.
            return traffic_unavailable(await self._health(row, conn))

    async def _facts(
        self, row: Server, conn: ManagedConnection | None
    ) -> tuple[
        Sourced[ClientFacts],
        Sourced[JetStreamAccountFacts],
        TelemetrySources,
        Sourced[TrafficFacts],
    ]:
        """The four independent reads behind both server panels, run together.

        Two are round trips to NATS and two go through the monitoring poller;
        none depends on another, so serialising them only added their latencies
        together on a screen that polls.

        `rtt_ms` is deliberately *not* one of them. `client_facts` already pings
        and caches the answer, so the caller asking afterwards gets it free --
        whereas asking concurrently would put a second PING on the wire for a
        number we were about to have.
        """
        if conn is None:
            sources, traffic = await asyncio.gather(
                self._sources(row, None), self._traffic(row, None)
            )
            no_account: Sourced[JetStreamAccountFacts] = Sourced.missing(
                Source.JETSTREAM, Reason.NOT_CONNECTED
            )
            return _no_client(), no_account, sources, traffic
        return await asyncio.gather(
            conn.client_facts(),
            conn.jetstream_account(),
            self._sources(row, conn),
            self._traffic(row, conn),
        )

    async def _summary(self, row: Server) -> ServerSummary:
        conn = self._conn.peek(row.id)
        client, account, sources, traffic = await self._facts(row, conn)
        rtt = await conn.rtt_ms() if conn else Sourced.missing(Source.CLIENT, Reason.NOT_CONNECTED)
        return ServerSummary(
            id=row.id,
            name=row.name,
            group=row.group.name if row.group else None,
            colour=row.colour,
            primary_url=row.urls[0] if row.urls else "",
            url_count=len(row.urls),
            state=conn.state if conn else ConnectionState.DISCONNECTED,
            note=summary_note(
                auth_mode=AuthMode(row.auth_mode),
                url_count=len(row.urls),
                client=client,
                nodes=conn.node_count() if conn else len(row.urls),
            ),
            last_error=conn.last_error if conn else None,
            telemetry_tag=sources.tag,
            rtt=rtt,
            jetstream=account,
            traffic=traffic,
        )

    async def _detail(self, row: Server) -> ServerDetail:
        conn = self._conn.peek(row.id)
        client, account, sources, traffic = await self._facts(row, conn)
        return ServerDetail(
            id=row.id,
            name=row.name,
            group=row.group.name if row.group else None,
            colour=row.colour,
            urls=tuple(row.urls),
            state=conn.state if conn else ConnectionState.DISCONNECTED,
            last_error=conn.last_error if conn else None,
            client=client,
            connection_rows=connection_rows(client),
            jetstream=account,
            jetstream_rows=jetstream_rows(account),
            traffic=traffic,
            sources=sources,
        )

    # -------------------------------------------------------------- writing

    async def create(self, data: ServerCreate) -> ServerConfig:
        return to_config(await self._repo.create(data))

    async def update(self, server_id: uuid.UUID, data: ServerUpdate) -> ServerConfig:
        row = await self._require(server_id)
        config = to_config(await self._repo.update(row, data))
        # The saved fields are the ones the socket was built from, so a live
        # connection is rebuilt rather than left running against stale settings.
        await self._conn.reload(server_id)
        return config

    async def delete(self, server_id: uuid.UUID) -> None:
        row = await self._require(server_id)
        await self._conn.forget(server_id)
        await self._repo.delete(row)

    async def connect(self, server_id: uuid.UUID) -> ServerDetail:
        """Open now, and report either way.

        A failure is not a 5xx. The design draws an offline card with the exception
        on it, so an unreachable server is a state of the resource rather than an
        error in the request that asked about it.
        """
        row = await self._require(server_id)
        try:
            await self._conn.connect(server_id)
        except Exception as exc:
            conn = self._conn.peek(server_id)
            if conn is None:
                # The attempt never got as far as a state machine, so there is no
                # card to put the error on. That one really is a failed request.
                raise NatsProblem.of(exc, instance=f"/api/servers/{server_id}/connect") from exc
            conn.record_error(exc)
        return await self._detail(row)

    async def disconnect(self, server_id: uuid.UUID) -> ServerDetail:
        row = await self._require(server_id)
        await self._conn.disconnect(server_id)
        return await self._detail(row)

    # ---------------------------------------------------------------- probe

    async def probe(self, data: ProbeRequest) -> ProbeResponse:
        """Two independent checks, run together.

        The design shows two result cards because either half can pass without the
        other: a server with no monitoring port is perfectly usable, and a monitoring
        port that answers proves nothing about the credentials.
        """
        client, monitoring = await asyncio.gather(
            self._probe_client(data), self._probe_monitoring(data)
        )
        return ProbeResponse(client=client, monitoring=monitoring)

    async def _probe_client(self, data: ProbeRequest) -> ProbeResult:
        if not data.urls:
            return ProbeResult(
                target=ProbeTarget.CLIENT,
                ok=False,
                title="No client URL",
                body="nats-lens needs at least one nats://, tls://, ws:// or wss:// URL.",
            )
        auth = _probe_auth(data)
        tls = TlsSpec(
            enabled=data.tls.enabled,
            verify=data.tls.verify,
            ca_path=data.tls.ca_path,
            cert_path=data.tls.cert_path,
            key_path=data.tls.key_path,
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            kwargs = connect_kwargs(
                auth,
                tls,
                name="nats-lens-probe",
                connect_timeout=self._settings.connect_timeout_seconds,
                allow_reconnect=False,
            )
            nc = await self._conn.connector(servers=list(data.urls), **kwargs)
        except Exception as exc:
            return ProbeResult(
                target=ProbeTarget.CLIENT,
                ok=False,
                title="Client could not connect",
                body=_probe_body(exc),
                detail=classify(exc)[1],
                latency_ms=round((loop.time() - started) * 1000, 1),
                error=describe(exc),
            )

        latency = round((loop.time() - started) * 1000, 1)
        info = getattr(nc, "_server_info", {}) or {}
        nodes = len(nc.servers) or len(data.urls)
        url = nc.connected_url
        facts = [
            f"nats-server {info.get('version', 'unknown')}",
            f"cluster {info['cluster']}" if info.get("cluster") else "no cluster",
            "single node" if nodes <= 1 else f"{nodes} nodes",
            "JetStream enabled" if info.get("jetstream") else "JetStream not enabled",
            f"max payload {format_bytes(int(nc.max_payload or 0))}",
        ]
        with contextlib.suppress(Exception):
            await nc.close()
        return ProbeResult(
            target=ProbeTarget.CLIENT,
            ok=True,
            title=f"Client connected in {format_ms(latency)}",
            body=" · ".join(facts),
            detail=f"Connected to {url.geturl() if url is not None else data.urls[0]}",
            latency_ms=latency,
        )

    async def _probe_monitoring(self, data: ProbeRequest) -> ProbeResult:
        url = data.monitoring_url
        if not url:
            suggestion = derive_monitoring_url(data.urls[0]) if data.urls else None
            return ProbeResult(
                target=ProbeTarget.MONITORING,
                ok=False,
                title="No monitoring URL",
                body=(
                    "Server-wide counters, connections, routes and health are HTTP only. "
                    "Without a monitoring URL the Servers screen names the fix instead of "
                    "showing a number."
                ),
                detail=f"Try {suggestion}" if suggestion else None,
            )

        health = await self._monitor_probe(url, self._settings.monitor_timeout_seconds)
        if health.reachable:
            latency = health.latency_ms or 0.0
            return ProbeResult(
                target=ProbeTarget.MONITORING,
                ok=True,
                title="Monitoring port answered",
                body=(
                    f"/healthz returned 200 in {format_ms(latency)}. Connections, routes, "
                    "slow consumers and health checks will all be available."
                ),
                detail=f"{url.rstrip('/')}/healthz",
                latency_ms=health.latency_ms,
            )
        return ProbeResult(
            target=ProbeTarget.MONITORING,
            ok=False,
            title="Monitoring port did not answer",
            body=(
                "The server is probably started without http_port, or the port is not open "
                "to nats-lens. Saving is still fine."
            ),
            detail=(
                "Without it: no connection list, no route table, no slow-consumer count, no "
                "throughput. Core NATS, JetStream, KV and the object store all keep working."
            ),
            error=health.error,
        )


def _no_client() -> Sourced[ClientFacts]:
    return Sourced.missing(Source.CLIENT, Reason.NOT_CONNECTED)


def _probe_body(exc: BaseException) -> str:
    if isinstance(exc, AuthError):
        return str(exc)
    name = type(exc).__name__
    for candidate, body in _PROBE_BODIES:
        if candidate == name:
            return body
    return _PROBE_FALLBACK


def _probe_auth(data: ProbeRequest) -> AuthSpec:
    """The form's secrets arrive in plaintext and are used once, in memory."""
    values = {s.kind: s.value for s in data.secrets}
    return AuthSpec(
        mode=data.auth_mode,
        username=data.username,
        password=values.get(SecretKind.PASSWORD),
        token=values.get(SecretKind.TOKEN),
        creds_text=values.get(SecretKind.CREDS),
        creds_path=data.creds_path,
        nkey_seed=values.get(SecretKind.NKEY_SEED),
        jwt=values.get(SecretKind.JWT),
    )


def provide_server_service(
    session: NamedDependency[AsyncSession], connections: NamedDependency[ConnectionManager]
) -> ServerService:
    """Litestar dependency. One repository per request, one manager per process."""
    vault = SecretVault(connections.settings.secret_key)
    return ServerService(ServerRepository(session, vault), connections, session)
