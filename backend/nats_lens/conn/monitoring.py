"""The HTTP monitoring port, read with httpx2.

A NATS client cannot see what this file reads. Connections, subscriptions, slow
consumers, routes and throughput are published only by nats-server's own HTTP
endpoints -- `/varz`, `/connz`, `/routez`, `/jsz`, `/healthz` -- which are off
until someone starts the server with `-m 8222`. That is the whole reason
`Source.MONITOR` exists, and the reason a failure here has to be reported rather
than smoothed over.

Two decisions follow from that:

* Every call returns its status code and how long it took, failures included, so
  the Monitor screen can show `200 - 41 ms` next to the URL and the user can tell
  a slow port from a closed one.
* A non-200 from `/healthz` is a result, not an exception. 503 is exactly the
  signal an orchestrator reads, and the Health tab renders it as a row.

Responses are decoded with msgspec into the `Raw*` structs below. They are
deliberately partial and every field has a default: nats-server adds keys between
releases and drops others when a subsystem is off, and a monitoring client that
breaks on an unfamiliar version is worse than one that shows what it recognised.
"""

from __future__ import annotations

import time
from typing import Any, Generic, TypeVar

import httpx2
import msgspec

from nats_lens.domain.monitor.schemas import (
    ConnRow,
    ConnzPage,
    ConnzQuery,
    EndpointResult,
    HealthCheck,
    HealthQuery,
    JszSummary,
    RouteRow,
    RoutezSummary,
    SubRow,
    SubszQuery,
    SubszSummary,
    VarzSummary,
)

T = TypeVar("T")

_MAX_CAUSE_DEPTH = 8
"""How far to dig into an exception chain for the errno. Guards against cycles."""


# ------------------------------------------------------------------ raw payloads


class RawApiStats(msgspec.Struct, rename=None):
    total: int = 0
    errors: int = 0


class RawJetStreamConfig(msgspec.Struct):
    store_dir: str | None = None
    max_memory: int = 0
    max_storage: int = 0


class RawJetStreamStats(msgspec.Struct):
    memory: int = 0
    storage: int = 0
    accounts: int = 0
    ha_assets: int = 0
    api: RawApiStats = msgspec.field(default_factory=RawApiStats)


class RawJetStream(msgspec.Struct):
    config: RawJetStreamConfig | None = None
    stats: RawJetStreamStats | None = None


class RawVarz(msgspec.Struct):
    """`/varz`. The only place a server publishes its own counters."""

    server_id: str = ""
    server_name: str = ""
    version: str = ""
    start: str = ""
    now: str = ""
    uptime: str = ""
    connections: int = 0
    total_connections: int = 0
    routes: int = 0
    remotes: int = 0
    leafnodes: int = 0
    subscriptions: int = 0
    slow_consumers: int = 0
    in_msgs: int = 0
    out_msgs: int = 0
    in_bytes: int = 0
    out_bytes: int = 0
    mem: int = 0
    cpu: float = 0.0
    cores: int = 0
    max_payload: int = 0
    jetstream: RawJetStream | None = None


class RawConn(msgspec.Struct):
    cid: int = 0
    kind: str = "Client"
    ip: str = ""
    port: int = 0
    subscriptions: int = 0
    pending_bytes: int = 0
    in_msgs: int = 0
    out_msgs: int = 0
    idle: str = ""
    uptime: str = ""
    rtt: str | None = None
    name: str | None = None
    account: str | None = None
    lang: str | None = None
    version: str | None = None
    subscriptions_list: tuple[str, ...] = ()


class RawConnz(msgspec.Struct):
    now: str = ""
    num_connections: int = 0
    total: int = 0
    offset: int = 0
    limit: int = 0
    connections: tuple[RawConn, ...] = ()


class RawRoute(msgspec.Struct):
    rid: int = 0
    ip: str = ""
    port: int = 0
    subscriptions: int = 0
    pending_size: int = 0
    in_msgs: int = 0
    out_msgs: int = 0
    rtt: str | None = None
    remote_id: str | None = None
    remote_name: str | None = None


class RawSub(msgspec.Struct):
    """One entry from `/subsz?subs=true`."""

    subject: str = ""
    sid: str = ""
    account: str | None = None
    cid: int | None = None
    msgs: int = 0
    queue_group: str | None = None


class RawSubsz(msgspec.Struct):
    now: str = ""
    num_subscriptions: int = 0
    num_cache: int = 0
    num_inserts: int = 0
    num_removes: int = 0
    num_matches: int = 0
    cache_hit_rate: float = 0.0
    max_fanout: int = 0
    avg_fanout: float = 0.0
    total: int = 0
    offset: int = 0
    limit: int = 0
    subscriptions_list: tuple[RawSub, ...] = ()


class RawRoutez(msgspec.Struct):
    now: str = ""
    num_routes: int = 0
    routes: tuple[RawRoute, ...] = ()


class RawLeaf(msgspec.Struct):
    """One `/leafz` entry. nats-server publishes no connection id for a leaf."""

    name: str | None = None
    account: str | None = None
    ip: str = ""
    port: int = 0
    subscriptions: int = 0
    in_msgs: int = 0
    out_msgs: int = 0
    rtt: str | None = None
    is_spoke: bool = False


class RawLeafz(msgspec.Struct):
    now: str = ""
    leafnodes: int = 0
    leafs: tuple[RawLeaf, ...] = ()


class RawGatewayConn(msgspec.Struct):
    cid: int = 0
    ip: str = ""
    port: int = 0
    subscriptions: int = 0
    pending_bytes: int = 0
    in_msgs: int = 0
    out_msgs: int = 0
    rtt: str | None = None


class RawGateway(msgspec.Struct):
    name: str = ""
    connection: RawGatewayConn | None = None


class RawGatewayz(msgspec.Struct):
    now: str = ""
    name: str = ""
    outbound_gateways: dict[str, RawGateway] = msgspec.field(default_factory=dict)
    inbound_gateways: dict[str, tuple[RawGateway, ...]] = msgspec.field(default_factory=dict)


class RawMetaCluster(msgspec.Struct):
    name: str | None = None
    leader: str | None = None
    cluster_size: int = 0


class RawJsz(msgspec.Struct):
    now: str = ""
    disabled: bool = False
    memory: int = 0
    storage: int = 0
    accounts: int = 0
    ha_assets: int = 0
    api: RawApiStats = msgspec.field(default_factory=RawApiStats)
    streams: int = 0
    consumers: int = 0
    messages: int = 0
    bytes: int = 0
    meta_cluster: RawMetaCluster | None = None


class RawHealthzError(msgspec.Struct):
    type: str | None = None
    error: str | None = None
    account: str | None = None
    stream: str | None = None
    consumer: str | None = None


class RawHealthz(msgspec.Struct):
    """`/healthz` in both of its shapes: one `error`, or a list of them."""

    status: str | None = None
    error: str | None = None
    errors: tuple[RawHealthzError, ...] = ()

    def summarise(self) -> str | None:
        if self.error:
            return self.error
        if not self.errors:
            return None
        return "; ".join(e.error for e in self.errors if e.error) or None


_VARZ = msgspec.json.Decoder(RawVarz)
_CONNZ = msgspec.json.Decoder(RawConnz)
_ROUTEZ = msgspec.json.Decoder(RawRoutez)
_SUBSZ = msgspec.json.Decoder(RawSubsz)
_LEAFZ = msgspec.json.Decoder(RawLeafz)
_GATEWAYZ = msgspec.json.Decoder(RawGatewayz)
_JSZ = msgspec.json.Decoder(RawJsz)
_HEALTHZ = msgspec.json.Decoder(RawHealthz)


# ------------------------------------------------------------------ call results


# UP046 wants PEP 695 `class Fetched[T]`. Do not: this module uses postponed
# annotations, and a PEP 695 parameter is scoped to the class rather than the
# module, so msgspec cannot resolve the forward reference. Same reasoning as
# `Sourced` in provenance.py.
class Fetched(msgspec.Struct, Generic[T], frozen=True):  # noqa: UP046
    """A decoded payload and the receipt for the call that produced it."""

    value: T
    result: EndpointResult


class MonitoringError(Exception):
    """A monitoring call that produced no usable payload.

    Carries the same `EndpointResult` a success would have, so a failure is still
    a measured call -- the Monitor screen shows the status code and the latency of
    a refused connection exactly as it shows those of a good one.
    """

    def __init__(self, result: EndpointResult) -> None:
        super().__init__(result.error or f"{result.path} answered {result.status_code}")
        self.result = result

    @property
    def detail(self) -> str:
        """The sentence appended to the fix. Contains the errno where there is one."""
        return self.result.error or f"{self.result.path} answered {self.result.status_code}."


def describe_error(exc: BaseException) -> str:
    """Render an exception as the sentence a user can act on.

    httpx wraps the socket's `OSError`, and the errno inside it is the part that
    separates a closed port from a filtered one from a wrong hostname. Reporting
    only `ConnectError` would throw away the single most useful fact, so the cause
    chain is walked for the first errno and that is what reaches the fix text.
    """
    cause: BaseException | None = exc
    for _ in range(_MAX_CAUSE_DEPTH):
        if cause is None:
            break
        if isinstance(cause, OSError) and cause.errno is not None:
            strerror = cause.strerror or str(cause) or "no further detail"
            return f"{type(exc).__name__}: [Errno {cause.errno}] {strerror}."
        cause = cause.__cause__ or cause.__context__
    text = str(exc).strip()
    if not text:
        return f"{type(exc).__name__}."
    return f"{type(exc).__name__}: {text}" + ("" if text.endswith((".", "!", "?")) else ".")


def display_path(path: str, params: dict[str, Any] | None = None) -> str:
    """The path as the design prints it, query string and all.

    The Health tab and the connections footer both show the exact request that was
    made, which is the difference between a screen you can trust and one you have
    to reproduce by hand.
    """
    path = "/" + path.lstrip("/")
    if not params:
        return path
    return f"{path}?{httpx2.QueryParams(params)}"


# ------------------------------------------------------------------ conversions


def to_varz_summary(raw: RawVarz) -> VarzSummary:
    return VarzSummary(
        server_id=raw.server_id,
        server_name=raw.server_name,
        version=raw.version,
        uptime=raw.uptime,
        start=raw.start,
        connections=raw.connections,
        total_connections=raw.total_connections,
        routes=raw.routes,
        remotes=raw.remotes,
        leafnodes=raw.leafnodes,
        subscriptions=raw.subscriptions,
        slow_consumers=raw.slow_consumers,
        in_msgs=raw.in_msgs,
        out_msgs=raw.out_msgs,
        in_bytes=raw.in_bytes,
        out_bytes=raw.out_bytes,
        mem=raw.mem,
        cpu=raw.cpu,
        cores=raw.cores,
        max_payload=raw.max_payload,
        # `jetstream` is present but empty on a server built with JetStream and
        # started without it, so the config block -- not the key -- is the signal.
        jetstream_enabled=raw.jetstream is not None and raw.jetstream.config is not None,
    )


def to_conn_row(raw: RawConn) -> ConnRow:
    return ConnRow(
        cid=raw.cid,
        account=raw.account,
        name=raw.name,
        kind=raw.kind,
        ip=raw.ip,
        port=raw.port,
        subscriptions=raw.subscriptions,
        pending_bytes=raw.pending_bytes,
        in_msgs=raw.in_msgs,
        out_msgs=raw.out_msgs,
        rtt=raw.rtt,
        idle=raw.idle,
        uptime=raw.uptime,
        lang=raw.lang,
        version=raw.version,
        subjects=raw.subscriptions_list,
    )


def to_connz_page(raw: RawConnz) -> ConnzPage:
    return ConnzPage(
        now=raw.now,
        num_connections=raw.num_connections,
        total=raw.total,
        offset=raw.offset,
        limit=raw.limit,
        connections=tuple(to_conn_row(c) for c in raw.connections),
    )


def to_subsz_summary(raw: RawSubsz) -> SubszSummary:
    return SubszSummary(
        now=raw.now,
        num_subscriptions=raw.num_subscriptions,
        num_cache=raw.num_cache,
        num_inserts=raw.num_inserts,
        num_removes=raw.num_removes,
        num_matches=raw.num_matches,
        cache_hit_rate=raw.cache_hit_rate,
        max_fanout=raw.max_fanout,
        avg_fanout=raw.avg_fanout,
        total=raw.total,
        offset=raw.offset,
        limit=raw.limit,
        subscriptions=tuple(
            SubRow(
                account=s.account,
                subject=s.subject,
                sid=s.sid,
                cid=s.cid,
                msgs=s.msgs,
                queue_group=s.queue_group,
            )
            for s in raw.subscriptions_list
        ),
    )


def to_routez_summary(
    routez: RawRoutez,
    leafz: RawLeafz | None = None,
    gatewayz: RawGatewayz | None = None,
) -> RoutezSummary:
    """One table from three endpoints.

    The design shows routes, leaf nodes and gateways in a single list because that
    is how an operator thinks about a cluster's edges. nats-server splits them
    across `/routez`, `/leafz` and `/gatewayz`, so they are merged here.
    """
    rows: list[RouteRow] = [
        RouteRow(
            rid=r.rid,
            remote_id=r.remote_name or r.remote_id,
            ip=r.ip,
            port=r.port,
            subscriptions=r.subscriptions,
            pending_size=r.pending_size,
            in_msgs=r.in_msgs,
            out_msgs=r.out_msgs,
            rtt=r.rtt,
            kind="route",
        )
        for r in routez.routes
    ]

    leafs = leafz.leafs if leafz else ()
    rows.extend(
        RouteRow(
            # `/leafz` publishes no connection id, so there is none to show. A
            # fabricated one would look like a fact.
            rid=0,
            remote_id=leaf.name or leaf.account,
            ip=leaf.ip,
            port=leaf.port,
            subscriptions=leaf.subscriptions,
            pending_size=0,
            in_msgs=leaf.in_msgs,
            out_msgs=leaf.out_msgs,
            rtt=leaf.rtt,
            kind="leaf",
        )
        for leaf in leafs
    )

    gateways: list[RawGateway] = []
    if gatewayz is not None:
        gateways.extend(gatewayz.outbound_gateways.values())
        for inbound in gatewayz.inbound_gateways.values():
            gateways.extend(inbound)
    rows.extend(
        RouteRow(
            rid=gw.connection.cid if gw.connection else 0,
            remote_id=gw.name or None,
            ip=gw.connection.ip if gw.connection else "",
            port=gw.connection.port if gw.connection else 0,
            subscriptions=gw.connection.subscriptions if gw.connection else 0,
            pending_size=gw.connection.pending_bytes if gw.connection else 0,
            in_msgs=gw.connection.in_msgs if gw.connection else 0,
            out_msgs=gw.connection.out_msgs if gw.connection else 0,
            rtt=gw.connection.rtt if gw.connection else None,
            kind="gateway",
        )
        for gw in gateways
    )

    return RoutezSummary(
        now=routez.now,
        num_routes=routez.num_routes,
        num_leafnodes=leafz.leafnodes if leafz else 0,
        num_gateways=len(gateways),
        routes=tuple(rows),
    )


def to_jsz_summary(raw: RawJsz) -> JszSummary:
    return JszSummary(
        meta_leader=raw.meta_cluster.leader if raw.meta_cluster else None,
        streams=raw.streams,
        consumers=raw.consumers,
        messages=raw.messages,
        bytes=raw.bytes,
        memory=raw.memory,
        storage=raw.storage,
        api_total=raw.api.total,
        api_errors=raw.api.errors,
        disabled=raw.disabled,
    )


def connz_params(query: ConnzQuery) -> dict[str, Any]:
    """`/connz`'s real query surface. `account` is spelled `acc` on the wire."""
    params: dict[str, Any] = {
        "sort": query.sort,
        "limit": query.limit,
        "offset": query.offset,
        "subs": "true" if query.subs else "false",
        "auth": "true" if query.auth else "false",
        "state": query.state,
    }
    if query.account:
        params["acc"] = query.account
    return params


def healthz_params(query: HealthQuery) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if query.js_enabled_only:
        params["js-enabled-only"] = "true"
    if query.js_server_only:
        params["js-server-only"] = "true"
    if query.stream:
        params["stream"] = query.stream
    if query.consumer:
        params["consumer"] = query.consumer
    if query.account:
        params["account"] = query.account
    return params


# ------------------------------------------------------------------ the client


class MonitoringClient:
    """One `httpx2.AsyncClient`, bound to one server's monitoring base URL.

    Bound rather than shared so connection reuse, timeout and TLS settings follow
    the server they belong to, and so closing a server's poller closes its sockets.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 5.0,
        client: httpx2.AsyncClient | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx2.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
            headers={"accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> MonitoringClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _call(
        self,
        path: str,
        decoder: msgspec.json.Decoder[T],
        *,
        description: str,
        params: dict[str, Any] | None = None,
    ) -> Fetched[T]:
        """Fetch, time, decode. Any of those failing still yields a receipt."""
        shown = display_path(path, params)
        started = time.perf_counter()
        try:
            response = await self._client.get(path, params=params)
        except Exception as exc:
            raise MonitoringError(
                EndpointResult(
                    path=shown,
                    status_code=0,
                    latency_ms=_elapsed_ms(started),
                    ok=False,
                    description=description,
                    error=describe_error(exc),
                )
            ) from exc

        latency = _elapsed_ms(started)
        if response.status_code != 200:
            raise MonitoringError(
                EndpointResult(
                    path=shown,
                    status_code=response.status_code,
                    latency_ms=latency,
                    ok=False,
                    description=description,
                    error=_body_error(response),
                )
            )
        try:
            value = decoder.decode(response.content)
        except msgspec.DecodeError as exc:
            raise MonitoringError(
                EndpointResult(
                    path=shown,
                    status_code=response.status_code,
                    latency_ms=latency,
                    ok=False,
                    description=description,
                    error=(
                        f"{shown} answered 200 but the body was not the JSON this "
                        f"version expects: {exc}."
                    ),
                )
            ) from exc

        return Fetched(
            value=value,
            result=EndpointResult(
                path=shown,
                status_code=response.status_code,
                latency_ms=latency,
                ok=True,
                description=description,
            ),
        )

    async def varz(self) -> Fetched[RawVarz]:
        return await self._call("/varz", _VARZ, description="server-wide counters")

    async def connz(self, query: ConnzQuery | None = None) -> Fetched[RawConnz]:
        return await self._call(
            "/connz",
            _CONNZ,
            description="client connections",
            params=connz_params(query or ConnzQuery()),
        )

    async def subsz(self, query: SubszQuery | None = None) -> Fetched[RawSubsz]:
        """The subscription interest graph.

        `test` makes this a question rather than a listing: given a subject, the
        server answers which subscriptions would match it -- the only way to see
        whether a publish has anyone to go to.
        """
        q = query or SubszQuery()
        params: dict[str, str] = {
            "subs": "true" if q.subs else "false",
            "offset": str(q.offset),
            "limit": str(q.limit),
        }
        if q.account:
            params["acc"] = q.account
        if q.test:
            params["test"] = q.test
        return await self._call(
            "/subsz", _SUBSZ, description="subscription interest", params=params
        )

    async def routez(self, *, subscriptions: bool = False) -> Fetched[RawRoutez]:
        return await self._call(
            "/routez",
            _ROUTEZ,
            description="cluster routes",
            params={"subs": "true"} if subscriptions else None,
        )

    async def leafz(self, *, subscriptions: bool = False) -> Fetched[RawLeafz]:
        return await self._call(
            "/leafz",
            _LEAFZ,
            description="leaf nodes",
            params={"subs": "true"} if subscriptions else None,
        )

    async def gatewayz(self) -> Fetched[RawGatewayz]:
        return await self._call("/gatewayz", _GATEWAYZ, description="gateways")

    async def jsz(
        self,
        *,
        streams: bool = False,
        consumers: bool = False,
        accounts: bool = False,
    ) -> Fetched[RawJsz]:
        """`/jsz`.

        With `streams` and `consumers` this returns per-stream and per-consumer
        state in one request, which is cheaper than walking the JetStream API when
        all the screen needs is an overview.
        """
        params: dict[str, Any] = {}
        if streams:
            params["streams"] = "true"
        if consumers:
            params["consumers"] = "true"
        if accounts:
            params["accounts"] = "true"
        return await self._call("/jsz", _JSZ, description="JetStream totals", params=params or None)

    async def healthz(self, query: HealthQuery | None = None, *, label: str = "") -> HealthCheck:
        """A health probe. Any HTTP answer is a result, including 503.

        This is the one call that does not raise on a non-200: 503 is what an
        orchestrator reads to decide whether to route traffic here, so the Health
        tab shows it as a row rather than an error. A connection that never
        completes is still a failure and still raises.
        """
        query = query or HealthQuery()
        params = healthz_params(query)
        shown = display_path("/healthz", params)
        started = time.perf_counter()
        try:
            response = await self._client.get("/healthz", params=params)
        except Exception as exc:
            raise MonitoringError(
                EndpointResult(
                    path=shown,
                    status_code=0,
                    latency_ms=_elapsed_ms(started),
                    ok=False,
                    description=label or "health check",
                    error=describe_error(exc),
                )
            ) from exc

        latency = _elapsed_ms(started)
        try:
            body = _HEALTHZ.decode(response.content)
        except msgspec.DecodeError:
            body = RawHealthz(status=None, error=response.text.strip() or None)

        return HealthCheck(
            path=shown,
            label=label or _health_label(query),
            status_code=response.status_code,
            latency_ms=latency,
            ok=response.status_code == 200,
            status=body.status,
            error=body.summarise(),
        )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _body_error(response: httpx2.Response) -> str:
    """What a non-200 said, if it said anything worth repeating."""
    text = response.text.strip()
    if not text:
        return f"The monitoring port answered {response.status_code} with an empty body."
    if len(text) > 300:
        text = text[:297] + "..."
    return f"The monitoring port answered {response.status_code}: {text}"


def _health_label(query: HealthQuery) -> str:
    """The plain-language column the design puts beside each health row."""
    if query.js_enabled_only:
        return "JetStream is enabled in the config"
    if query.js_server_only:
        return "this node is current, streams not checked"
    if query.stream and query.consumer:
        return f"consumer {query.consumer} on stream {query.stream}"
    if query.stream:
        return f"all replicas of stream {query.stream}"
    return "server and JetStream both ready"
