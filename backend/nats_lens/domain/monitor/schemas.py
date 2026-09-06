"""The HTTP monitoring port. Everything here is `monitor` provenance.

FROZEN CONTRACT -- see domain/common.py. nats-lens keeps no time series: this is
a live view, and the UI says so and points at prometheus-nats-exporter for history.
"""

from __future__ import annotations

import msgspec

from nats_lens.domain.common import KeyValueRow


class EndpointResult(msgspec.Struct, frozen=True):
    """One monitoring call, including the ones that fail. 503 is a result, not an error."""

    path: str
    status_code: int
    latency_ms: float
    ok: bool
    description: str
    error: str | None = None


class VarzSummary(msgspec.Struct, frozen=True):
    server_id: str
    server_name: str
    version: str
    uptime: str
    start: str
    connections: int
    total_connections: int
    routes: int
    remotes: int
    leafnodes: int
    subscriptions: int
    slow_consumers: int
    in_msgs: int
    out_msgs: int
    in_bytes: int
    out_bytes: int
    mem: int
    cpu: float
    cores: int
    max_payload: int
    jetstream_enabled: bool


class RateSample(msgspec.Struct, frozen=True):
    """A delta between the last two polls. Always `sampled`, never a server total."""

    in_msgs_per_sec: float
    out_msgs_per_sec: float
    in_bytes_per_sec: float
    out_bytes_per_sec: float
    window_ms: int


class JszSummary(msgspec.Struct, frozen=True):
    meta_leader: str | None
    streams: int
    consumers: int
    messages: int
    bytes: int
    memory: int
    storage: int
    api_total: int
    api_errors: int
    disabled: bool = False


class ConnRow(msgspec.Struct, frozen=True):
    cid: int
    account: str | None
    name: str | None
    kind: str
    ip: str
    port: int
    subscriptions: int
    pending_bytes: int
    in_msgs: int
    out_msgs: int
    rtt: str | None
    idle: str
    uptime: str
    lang: str | None = None
    version: str | None = None
    subjects: tuple[str, ...] = ()


class ConnzPage(msgspec.Struct, frozen=True):
    """`/connz` with its real query surface: sort, limit, offset, subs, auth."""

    now: str
    num_connections: int
    total: int
    offset: int
    limit: int
    connections: tuple[ConnRow, ...]


class ConnzQuery(msgspec.Struct, frozen=True):
    sort: str = "cid"
    limit: int = 100
    offset: int = 0
    subs: bool = False
    auth: bool = False
    account: str | None = None
    state: str = "open"


class RouteRow(msgspec.Struct, frozen=True):
    rid: int
    remote_id: str | None
    ip: str
    port: int
    subscriptions: int
    pending_size: int
    in_msgs: int
    out_msgs: int
    rtt: str | None
    kind: str
    """`route` | `leaf` | `gateway`."""


class RoutezSummary(msgspec.Struct, frozen=True):
    now: str
    num_routes: int
    num_leafnodes: int
    num_gateways: int
    routes: tuple[RouteRow, ...]


class SubRow(msgspec.Struct, frozen=True):
    """One subscription the server is holding, from `/subsz?subs=true`."""

    account: str | None
    subject: str
    sid: str
    cid: int | None
    msgs: int
    queue_group: str | None = None


class SubszSummary(msgspec.Struct, frozen=True):
    """The subscription interest graph, and how well the server is routing it.

    This is how "why is nobody receiving this?" gets answered: a subject with no
    matching subscription has no interest, and a core NATS publish to it is
    simply dropped. Nothing else in nats-lens can see that -- the client protocol
    does not expose another connection's subscriptions.

    `cache_hit_rate` and `max_fanout` are the routing health: a collapsing hit
    rate usually means subjects are being generated per-message rather than
    reused, which the server pays for on every publish.
    """

    now: str
    num_subscriptions: int
    num_cache: int
    num_inserts: int
    num_removes: int
    num_matches: int
    cache_hit_rate: float
    max_fanout: int
    avg_fanout: float
    total: int
    offset: int
    limit: int
    subscriptions: tuple[SubRow, ...] = ()


class SubszQuery(msgspec.Struct, frozen=True):
    """`/subsz` filters. `test` is the useful one, and it is not a listing at all.

    Given a subject, the server reports which subscriptions *would* match it --
    answering the interest question directly rather than making you read the
    whole table.
    """

    subs: bool = True
    offset: int = 0
    limit: int = 100
    account: str | None = None
    test: str | None = None
    """A concrete subject to match against the interest graph."""


class HealthCheck(msgspec.Struct, frozen=True):
    """One `/healthz` variant and what it answered."""

    path: str
    label: str
    status_code: int
    latency_ms: float
    ok: bool
    status: str | None = None
    error: str | None = None


class HealthQuery(msgspec.Struct, frozen=True):
    js_enabled_only: bool = False
    js_server_only: bool = False
    stream: str | None = None
    consumer: str | None = None
    account: str | None = None


class MonitorOverview(msgspec.Struct, frozen=True):
    url: str
    reachable: bool
    status_code: int | None
    latency_ms: float | None
    poll_seconds: float
    varz: VarzSummary | None
    rates: RateSample | None
    jsz: JszSummary | None
    varz_rows: tuple[KeyValueRow, ...] = ()
    error: str | None = None


class PrometheusHint(msgspec.Struct, frozen=True):
    """nats-lens is a live view. History belongs somewhere else, and it says where."""

    exporter_image: str
    exporter_command: str
    scrape_url: str
    surveyor_note: str
    grafana_dashboard_url: str
