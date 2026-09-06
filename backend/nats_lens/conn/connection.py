"""One registered server's live connections, and what can honestly be read from them.

A `ManagedConnection` owns up to two nats-py clients -- the application account and,
where it is configured, a second one bound to `$SYS` -- plus the JetStream context
and manager that ride on the first. Its other job is to answer, for every panel on
the Servers screen, either a value with its provenance or the reason there isn't one.

Connections are lazy. Nothing here is opened at import or at boot unless the server
was explicitly marked `connect_on_startup`, so a NATS server that is down cannot keep
nats-lens from starting.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import msgspec
from nats import errors as nats_errors
from nats.aio.client import Client as NATS
from nats.js import JetStreamContext, JetStreamManager
from nats.js import errors as js_errors

from nats_lens.conn.auth import AuthSpec, TlsSpec, connect_kwargs
from nats_lens.conn.errors import describe
from nats_lens.domain.servers.schemas import (
    ClientFacts,
    ConnectionState,
    JetStreamAccountFacts,
    TrafficFacts,
)
from nats_lens.provenance import Reason, Source, Sourced, Unavailable

Connector = Callable[..., Awaitable[NATS]]
"""`nats.connect`, or a stand-in. Injected so the whole state machine is testable
without a broker -- the unit suite exercises all five Servers-screen states this way."""

_RTT_TTL = 2.0
_ACCOUNT_TTL = 2.0
_HEALTH_TTL = 10.0
"""Short caches. The Servers list polls, and a PING or a JetStream API round trip per
server per poll would be a self-inflicted load test."""

_JETSTREAM_DOC = "https://docs.nats.io/nats-concepts/jetstream"
_MONITORING_DOC = "https://docs.nats.io/running-a-nats-service/nats_admin/monitoring"

_JS_DISABLED = (
    js_errors.ServiceUnavailableError,
    js_errors.NotFoundError,
    nats_errors.NoRespondersError,
)


def _now() -> datetime:
    return datetime.now(UTC)


class ConnectionSpec(msgspec.Struct, frozen=True):
    """One server, resolved: secrets opened, ready to hand to nats-py.

    Detached from the ORM on purpose. A connection outlives the request that opened
    it, and reading a lazily-loaded attribute off a closed session is the classic
    way that goes wrong.
    """

    server_id: uuid.UUID
    name: str
    urls: tuple[str, ...]
    auth: AuthSpec
    tls: TlsSpec
    monitoring_url: str | None = None
    monitoring_poll_seconds: float = 5.0
    system_account_enabled: bool = False
    system_auth: AuthSpec | None = None
    client_name: str = "nats-lens"
    inbox_prefix: str = "_INBOX"
    jetstream_domain: str | None = None
    max_reconnect_attempts: int = -1
    connect_on_startup: bool = False


class MonitoringHealth(msgspec.Struct, frozen=True):
    """One `/healthz` answer. Whether the `monitor` provenance source exists at all."""

    configured: bool
    reachable: bool
    detail: str
    checked_at: datetime
    latency_ms: float | None = None
    error: str | None = None


MonitorProbe = Callable[[str, float], Awaitable[MonitoringHealth]]


async def healthz(url: str, timeout: float) -> MonitoringHealth:
    """Is the monitoring port there?

    Deliberately the smallest possible question. Agent B3 owns the monitoring client
    and everything read through it; this only has to decide whether the source is
    reachable, which is what the telemetry card and the probe form both need.
    """
    import httpx2

    endpoint = f"{url.rstrip('/')}/healthz"
    started = asyncio.get_running_loop().time()
    try:
        async with httpx2.AsyncClient(timeout=timeout) as http:
            response = await http.get(endpoint)
    except Exception as exc:
        return MonitoringHealth(
            configured=True,
            reachable=False,
            detail="unreachable from here",
            checked_at=_now(),
            error=describe(exc),
        )
    latency = (asyncio.get_running_loop().time() - started) * 1000
    ok = response.status_code == 200
    return MonitoringHealth(
        configured=True,
        reachable=ok,
        detail=url if ok else f"answered {response.status_code}",
        checked_at=_now(),
        latency_ms=round(latency, 1),
        error=None if ok else f"HTTP {response.status_code} from {endpoint}",
    )


def not_configured() -> MonitoringHealth:
    return MonitoringHealth(
        configured=False, reachable=False, detail="not configured", checked_at=_now()
    )


class ManagedConnection:
    """The clients for one server, and the state machine nats-py drives.

    State is not polled. nats-py's `disconnected_cb` / `reconnected_cb` / `closed_cb`
    fire as the socket changes, so the sidebar dot reflects what actually happened
    rather than what was true at the last request.
    """

    def __init__(
        self,
        spec: ConnectionSpec,
        *,
        connect_timeout: float = 5.0,
        monitor_timeout: float = 5.0,
        connector: Connector | None = None,
        monitor_probe: MonitorProbe = healthz,
    ) -> None:
        self.spec = spec
        self._connect_timeout = connect_timeout
        self._monitor_timeout = monitor_timeout
        self._monitor_probe = monitor_probe
        self._connector = connector
        self._lock = asyncio.Lock()

        self._state = ConnectionState.DISCONNECTED
        self._last_error: str | None = None
        self._system_error: str | None = None
        self._connected_at: datetime | None = None
        self._changed_at = _now()
        self._closing = False

        self._nc: NATS | None = None
        self._sys_nc: NATS | None = None
        self._js: JetStreamContext | None = None
        self._jsm: JetStreamManager | None = None

        self._rtt: tuple[float, float] | None = None
        self._account: tuple[float, JetStreamAccountFacts | None, Exception | None] | None = None
        self._health: MonitoringHealth | None = None
        self._traffic: tuple[datetime, Sourced[TrafficFacts]] | None = None

    # ------------------------------------------------------------------ state

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def system_error(self) -> str | None:
        return self._system_error

    @property
    def connected_at(self) -> datetime | None:
        return self._connected_at

    @property
    def nc(self) -> NATS | None:
        return self._nc

    @property
    def sys_nc(self) -> NATS | None:
        return self._sys_nc

    @property
    def js(self) -> JetStreamContext | None:
        return self._js

    @property
    def jsm(self) -> JetStreamManager | None:
        return self._jsm

    @property
    def is_connected(self) -> bool:
        return self._nc is not None and self._nc.is_connected

    @property
    def system_connected(self) -> bool:
        return self._sys_nc is not None and self._sys_nc.is_connected

    def _set_state(self, state: ConnectionState) -> None:
        self._state = state
        self._changed_at = _now()

    def record_error(self, exc: BaseException) -> None:
        """Remember a failure in the exact words nats-py used.

        The Servers screen prints this verbatim, so `nats.errors.NoServersError`
        reaches the user as `nats.errors.NoServersError` and not as our paraphrase.
        """
        self._last_error = describe(exc)
        if not self.is_connected:
            self._set_state(ConnectionState.ERROR)

    # ------------------------------------------------------------- lifecycle

    async def open(self) -> None:
        """Open the client, and the `$SYS` client if one is configured.

        Raises whatever nats-py raised. Callers that want the failure on screen
        instead of in a response body catch it and read `last_error`.
        """
        async with self._lock:
            if self.is_connected:
                return
            self._closing = False
            self._set_state(ConnectionState.CONNECTING)
            connector = self._connector or default_connector()
            # nats-py retries the *initial* dial for as long as
            # `max_reconnect_attempts` allows, and -1 means forever;
            # `connect_timeout` bounds one TCP attempt, not the whole thing. So an
            # unreachable server would hang whichever request asked for it --
            # editing one, or pressing Connect -- with no way back. Reconnection
            # after a successful connect is still unlimited, which is what we want:
            # it is only the first dial that has a caller waiting on it.
            budget = self._connect_timeout * (len(self.spec.urls) + 1)
            # nats-py reports each failed attempt through `error_cb` before it
            # retries, so the specific reason is usually already recorded by the
            # time the budget runs out. Cleared first so a stale one from an
            # earlier attempt cannot be mistaken for this one's.
            self._last_error = None
            try:
                async with asyncio.timeout(budget):
                    self._nc = await connector(
                        servers=list(self.spec.urls),
                        error_cb=self._on_error,
                        disconnected_cb=self._on_disconnected,
                        reconnected_cb=self._on_reconnected,
                        closed_cb=self._on_closed,
                        **connect_kwargs(
                            self.spec.auth,
                            self.spec.tls,
                            name=self.spec.client_name,
                            inbox_prefix=self.spec.inbox_prefix,
                            connect_timeout=self._connect_timeout,
                            max_reconnect_attempts=self.spec.max_reconnect_attempts,
                        ),
                    )
            except TimeoutError as exc:
                self._nc = None
                # nats-py retries an authorization failure exactly like an
                # unreachable host, so without this the timeout would report "no
                # servers answered" for a server that answered and said no --
                # sending the user to check the network instead of the password.
                # `error_cb` has already recorded the real reason by now.
                specific = self._last_error
                detail = (
                    f"{specific}; gave up after {budget:g}s"
                    if specific
                    else (
                        f"none of the {len(self.spec.urls)} configured URLs "
                        f"answered within {budget:g}s"
                    )
                )
                failure = nats_errors.NoServersError(detail)
                if specific:
                    # Keep nats-py's own words for this failure; the budget is
                    # only the context in which we stopped waiting for them.
                    self._last_error = detail
                    self._set_state(ConnectionState.ERROR)
                else:
                    self.record_error(failure)
                raise failure from exc
            except BaseException as exc:
                self._nc = None
                self.record_error(exc)
                raise

            js_opts: dict[str, Any] = {}
            if self.spec.jetstream_domain:
                js_opts["domain"] = self.spec.jetstream_domain
            self._js = self._nc.jetstream(**js_opts)
            self._jsm = self._nc.jsm(**js_opts)

            self._last_error = None
            self._connected_at = _now()
            self._set_state(ConnectionState.CONNECTED)
            self._invalidate()

            if self.spec.system_account_enabled:
                if self.spec.system_auth is None:
                    self._system_error = (
                        "No usable $SYS credentials are stored for this server. Add a "
                        "system credentials file under the server's settings."
                    )
                else:
                    await self._open_system(connector)

    async def _open_system(self, connector: Connector) -> None:
        """The `$SYS` client is optional by construction.

        Losing it costs push events and STATSZ heartbeats; it must never cost the
        application connection, so a failure is recorded against the telemetry card
        and the app client carries on.
        """
        assert self.spec.system_auth is not None
        try:
            self._sys_nc = await connector(
                servers=list(self.spec.urls),
                **connect_kwargs(
                    self.spec.system_auth,
                    self.spec.tls,
                    name=f"{self.spec.client_name}-sys",
                    inbox_prefix=self.spec.inbox_prefix,
                    connect_timeout=self._connect_timeout,
                    max_reconnect_attempts=self.spec.max_reconnect_attempts,
                ),
            )
            self._system_error = None
        except BaseException as exc:
            self._sys_nc = None
            self._system_error = describe(exc)

    async def close(self) -> None:
        """Drain, then close. Anything already in flight gets delivered first."""
        async with self._lock:
            self._closing = True
            for client in (self._sys_nc, self._nc):
                await _drain_and_close(client)
            self._nc = self._sys_nc = None
            self._js = self._jsm = None
            self._connected_at = None
            self._invalidate()
            self._set_state(ConnectionState.DISCONNECTED)

    def _invalidate(self) -> None:
        self._rtt = None
        self._account = None

    # ------------------------------------------------------------- callbacks

    async def _on_error(self, exc: Exception) -> None:
        self.record_error(exc)

    async def _on_disconnected(self) -> None:
        if self._closing:
            return
        # nats-py keeps retrying unless reconnection was switched off, so the
        # honest label between the drop and the next attempt is `reconnecting`.
        retrying = self.spec.max_reconnect_attempts != 0
        self._set_state(ConnectionState.RECONNECTING if retrying else ConnectionState.DISCONNECTED)

    async def _on_reconnected(self) -> None:
        self._last_error = None
        self._connected_at = _now()
        self._invalidate()
        self._set_state(ConnectionState.CONNECTED)

    async def _on_closed(self) -> None:
        if self._closing:
            return
        self._set_state(ConnectionState.ERROR if self._last_error else ConnectionState.DISCONNECTED)

    # ------------------------------------------------------------------ facts

    def server_info(self) -> dict[str, Any]:
        """The server's INFO block.

        nats-py exposes `max_payload` and `connected_server_version` as properties but
        nothing for `server_id`, `server_name` or `cluster`, so the parsed INFO is read
        directly. It is a plain dict the client refreshes on every (re)connect.
        """
        if self._nc is None:
            return {}
        return getattr(self._nc, "_server_info", {}) or {}

    async def rtt_ms(self) -> Sourced[float]:
        if not self.is_connected or self._nc is None:
            return Sourced.missing(Source.CLIENT, Reason.NOT_CONNECTED)
        loop = asyncio.get_running_loop()
        if self._rtt is not None and loop.time() - self._rtt[0] < _RTT_TTL:
            return Sourced.known(self._rtt[1], Source.CLIENT)
        try:
            seconds = await self._nc.rtt(timeout=int(max(1, self._connect_timeout)))
        except Exception as exc:
            self.record_error(exc)
            return Sourced.missing(Source.CLIENT, Reason.NOT_CONNECTED, detail=describe(exc))
        value = round(seconds * 1000, 1)
        self._rtt = (loop.time(), value)
        return Sourced.known(value, Source.CLIENT)

    async def client_facts(self) -> Sourced[ClientFacts]:
        if not self.is_connected or self._nc is None:
            return Sourced.missing(Source.CLIENT, Reason.NOT_CONNECTED)
        info = self.server_info()
        rtt = await self.rtt_ms()
        url = self._nc.connected_url
        return Sourced.known(
            ClientFacts(
                server_id=str(info.get("server_id", "")),
                server_name=str(info.get("server_name", "")),
                version=str(info.get("version", "")),
                cluster=info.get("cluster") or None,
                rtt_ms=rtt.value if rtt.value is not None else 0.0,
                max_payload=int(self._nc.max_payload or 0),
                jetstream_enabled=bool(info.get("jetstream", False)),
                tls=bool(info.get("tls_required", False))
                or (url is not None and url.scheme in ("tls", "wss")),
                headers_supported=bool(info.get("headers", False)),
                connected_url=url.geturl() if url is not None else self.spec.urls[0],
            ),
            Source.CLIENT,
        )

    def node_count(self) -> int:
        """How many servers the client knows about, seeds plus anything discovered."""
        if self._nc is None:
            return len(self.spec.urls)
        return len(self._nc.servers) or len(self.spec.urls)

    async def jetstream_account(self) -> Sourced[JetStreamAccountFacts]:
        if not self.is_connected or self._js is None:
            return Sourced.missing(Source.JETSTREAM, Reason.NOT_CONNECTED)
        if not self.server_info().get("jetstream", False):
            return Sourced.missing(Source.JETSTREAM, Reason.JETSTREAM_NOT_ENABLED)

        loop = asyncio.get_running_loop()
        if self._account is not None and loop.time() - self._account[0] < _ACCOUNT_TTL:
            _, cached, failure = self._account
            if cached is not None:
                return Sourced.known(cached, Source.JETSTREAM)
            if failure is not None:
                return self._jetstream_unavailable(failure)

        try:
            info = await self._js.account_info()
        except Exception as exc:
            self._account = (loop.time(), None, exc)
            return self._jetstream_unavailable(exc)

        facts = JetStreamAccountFacts(
            streams=info.streams,
            consumers=info.consumers,
            memory_used=info.memory,
            storage_used=info.storage,
            memory_limit=info.limits.max_memory,
            storage_limit=info.limits.max_storage,
            api_total=info.api.total,
            api_errors=info.api.errors,
            domain=info.domain,
        )
        self._account = (loop.time(), facts, None)
        return Sourced.known(facts, Source.JETSTREAM)

    def _jetstream_unavailable(self, exc: Exception) -> Sourced[JetStreamAccountFacts]:
        if isinstance(exc, _JS_DISABLED):
            # The server and the account are two different "not enabled"s with two
            # different fixes, and NATS says which: `JetStream not enabled for
            # account` (10039) means the server has it and this account does not.
            # Telling someone to restart with `-js` when the server already runs it
            # sends them the wrong way -- which is the one thing this envelope exists
            # to prevent.
            if "for account" in str(exc).lower():
                return Sourced.missing(Source.JETSTREAM, Reason.JETSTREAM_NOT_ENABLED_FOR_ACCOUNT)
            return Sourced.missing(Source.JETSTREAM, Reason.JETSTREAM_NOT_ENABLED)
        # `Unavailable.of` has one fixed sentence per reason, and none of them fits
        # "the API answered with something we did not expect". Naming the actual
        # error is more useful than picking the closest stock sentence.
        return Sourced(
            value=None,
            source=Source.JETSTREAM,
            at=_now(),
            unavailable=Unavailable(
                reason=Reason.NOT_SUPPORTED_BY_SERVER,
                fix=f"The JetStream API did not answer. {describe(exc)}",
                doc=_JETSTREAM_DOC,
            ),
        )

    # -------------------------------------------------------------- telemetry

    async def monitoring_health(self, *, force: bool = False) -> MonitoringHealth:
        if self.spec.monitoring_url is None:
            self._health = not_configured()
            return self._health
        fresh = (
            self._health is not None
            and (_now() - self._health.checked_at).total_seconds() < _HEALTH_TTL
        )
        if fresh and not force and self._health is not None:
            return self._health
        self._health = await self._monitor_probe(self.spec.monitoring_url, self._monitor_timeout)
        return self._health

    def publish_traffic(self, facts: Sourced[TrafficFacts]) -> None:
        """SEAM: where agent B3's monitoring poller hands over its counters.

        B1 never reads `/varz` or `$SYS.SERVER.*.STATSZ`. Until B3 calls this, and
        whenever its last sample has gone stale, `traffic()` reports the source as
        unavailable with the fix -- which is the whole point of the provenance
        contract, and why it must never fall back to zeros.
        """
        self._traffic = (_now(), facts)

    def _traffic_sample(self) -> Sourced[TrafficFacts] | None:
        if self._traffic is None:
            return None
        at, facts = self._traffic
        # Three polls of silence means the poller stopped, not that traffic stopped.
        stale_after = max(3 * self.spec.monitoring_poll_seconds, 30.0)
        return facts if (_now() - at).total_seconds() < stale_after else None

    async def traffic(self) -> Sourced[TrafficFacts]:
        """Server-wide counters, or the reason there are none.

        Never a zero. `connections: 0` and "we cannot see connections from here" are
        different facts, and conflating them is the bug this whole product exists to
        avoid.
        """
        if (sample := self._traffic_sample()) is not None:
            return sample
        return traffic_unavailable(await self.monitoring_health())


def traffic_unavailable(health: MonitoringHealth) -> Sourced[TrafficFacts]:
    """Why there are no server-wide counters, in the words that name the fix.

    Shared with the Servers list, which reports on registered servers that have no
    open connection at all and so have no `ManagedConnection` to ask.
    """
    if not health.configured:
        return Sourced.missing(Source.MONITOR, Reason.MONITORING_NOT_CONFIGURED)
    if not health.reachable:
        return Sourced.missing(Source.MONITOR, Reason.MONITORING_UNREACHABLE, detail=health.error)
    # Configured, answering, but nothing has been read from it yet. That is a
    # different sentence from "did not answer", so it gets one of its own.
    return Sourced(
        value=None,
        source=Source.MONITOR,
        at=_now(),
        unavailable=Unavailable(
            reason=Reason.MONITORING_UNREACHABLE,
            fix=(
                "The monitoring URL answered, but no counters have been read from it "
                "yet. They appear on the next poll."
            ),
            doc=_MONITORING_DOC,
        ),
    )


def default_connector() -> Connector:
    import nats

    return nats.connect


async def _drain_and_close(client: NATS | None) -> None:
    """Drain first so in-flight messages land, then close.

    Both calls are best effort: a connection that is already gone raises from
    `drain()`, and shutdown is not the moment to care.
    """
    if client is None:
        return
    with contextlib.suppress(Exception):
        await client.drain()
    with contextlib.suppress(Exception):
        await client.close()
