"""Servers screen and the Add-a-server form.

FROZEN CONTRACT -- see domain/common.py.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

import msgspec

from nats_lens.crypto import SecretKind, SecretRef
from nats_lens.domain.common import KeyValueRow
from nats_lens.provenance import Sourced


class AuthMode(StrEnum):
    NONE = "none"
    USERPASS = "userpass"
    TOKEN = "token"
    CREDS = "creds"
    NKEY = "nkey"


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class TelemetrySource(msgspec.Struct, frozen=True):
    """One row of the design's 'Telemetry sources' card."""

    label: str
    configured: bool
    reachable: bool | None
    detail: str
    """Either the URL that answered, or the exact reason it did not."""


class TelemetrySources(msgspec.Struct, frozen=True):
    """Which provenance sources this server can actually serve.

    The Servers screen renders `both` / `monitoring` / `system` / `none` from this,
    and it is what decides whether a panel shows a number or an empty state.
    """

    monitoring: TelemetrySource
    system_account: TelemetrySource
    tag: str
    """`both` | `monitoring` | `system` | `none`."""
    note: str
    """One sentence on what this combination means for the numbers on screen."""


class ClientFacts(msgspec.Struct, frozen=True):
    """What the client connection alone can see. Always `client` provenance."""

    server_id: str
    server_name: str
    version: str
    cluster: str | None
    rtt_ms: float
    max_payload: int
    jetstream_enabled: bool
    tls: bool
    headers_supported: bool
    connected_url: str


class JetStreamAccountFacts(msgspec.Struct, frozen=True):
    """The JetStream account report. Always `jetstream` provenance."""

    streams: int
    consumers: int
    memory_used: int
    storage_used: int
    memory_limit: int
    storage_limit: int
    api_total: int
    api_errors: int
    domain: str | None = None


class TrafficFacts(msgspec.Struct, frozen=True):
    """Server-wide throughput. Only available via `monitor` or `system`."""

    in_msgs: int
    out_msgs: int
    in_bytes: int
    out_bytes: int
    connections: int
    subscriptions: int
    slow_consumers: int
    routes: int


class ServerSummary(msgspec.Struct, frozen=True):
    """A row in the servers table."""

    id: uuid.UUID
    name: str
    group: str | None
    colour: str
    primary_url: str
    url_count: int
    state: ConnectionState
    note: str
    """`cluster prod-east - 3 nodes - creds file`, built from what is actually known."""
    last_error: str | None
    telemetry_tag: str
    rtt: Sourced[float]
    jetstream: Sourced[JetStreamAccountFacts]
    traffic: Sourced[TrafficFacts]


class ServerDetail(msgspec.Struct, frozen=True):
    """The right-hand panel of the Servers screen."""

    id: uuid.UUID
    name: str
    group: str | None
    colour: str
    urls: tuple[str, ...]
    state: ConnectionState
    last_error: str | None
    client: Sourced[ClientFacts]
    connection_rows: tuple[KeyValueRow, ...]
    jetstream: Sourced[JetStreamAccountFacts]
    jetstream_rows: tuple[KeyValueRow, ...]
    traffic: Sourced[TrafficFacts]
    sources: TelemetrySources


class TlsConfig(msgspec.Struct, frozen=True):
    enabled: bool = False
    verify: bool = True
    ca_path: str | None = None
    cert_path: str | None = None
    key_path: str | None = None


class AdvancedConfig(msgspec.Struct, frozen=True):
    client_name: str = "nats-lens"
    inbox_prefix: str = "_INBOX"
    jetstream_domain: str | None = None
    max_reconnect_attempts: int = -1


class SecretInput(msgspec.Struct, frozen=True):
    """A secret on the way in. Write-only -- it is never echoed back."""

    kind: SecretKind
    value: str


class ServerCreate(msgspec.Struct, frozen=True):
    name: str
    urls: tuple[str, ...]
    group: str | None = None
    colour: str = "#a6b1ee"
    auth_mode: AuthMode = AuthMode.NONE
    username: str | None = None
    creds_path: str | None = None
    secrets: tuple[SecretInput, ...] = ()
    tls: TlsConfig = msgspec.field(default_factory=TlsConfig)
    monitoring_url: str | None = None
    monitoring_poll_seconds: float = 5.0
    system_account_enabled: bool = False
    system_username: str | None = None
    system_creds_path: str | None = None
    advanced: AdvancedConfig = msgspec.field(default_factory=AdvancedConfig)
    connect_on_startup: bool = False


class ServerUpdate(msgspec.Struct, frozen=True, omit_defaults=True):
    """Partial update. Unset fields are left alone; `secrets` replaces by kind."""

    name: str | None = None
    urls: tuple[str, ...] | None = None
    group: str | None = None
    colour: str | None = None
    auth_mode: AuthMode | None = None
    username: str | None = None
    creds_path: str | None = None
    secrets: tuple[SecretInput, ...] | None = None
    tls: TlsConfig | None = None
    monitoring_url: str | None = None
    monitoring_poll_seconds: float | None = None
    system_account_enabled: bool | None = None
    system_username: str | None = None
    system_creds_path: str | None = None
    advanced: AdvancedConfig | None = None
    connect_on_startup: bool | None = None


class ServerConfig(msgspec.Struct, frozen=True):
    """The saved form, read back. Secrets appear only as `SecretRef`."""

    id: uuid.UUID
    name: str
    group: str | None
    colour: str
    urls: tuple[str, ...]
    auth_mode: AuthMode
    username: str | None
    creds_path: str | None
    secrets: tuple[SecretRef, ...]
    tls: TlsConfig
    monitoring_url: str | None
    monitoring_poll_seconds: float
    system_account_enabled: bool
    system_username: str | None
    system_creds_path: str | None
    advanced: AdvancedConfig
    connect_on_startup: bool


class ProbeTarget(StrEnum):
    CLIENT = "client"
    MONITORING = "monitoring"


class ProbeResult(msgspec.Struct, frozen=True):
    """One half of the Add-a-server screen's two independent probe cards."""

    target: ProbeTarget
    ok: bool
    title: str
    body: str
    detail: str | None = None
    latency_ms: float | None = None
    error: str | None = None


class ProbeRequest(msgspec.Struct, frozen=True):
    """Probe before saving, so the form can report without creating anything."""

    urls: tuple[str, ...]
    monitoring_url: str | None = None
    auth_mode: AuthMode = AuthMode.NONE
    username: str | None = None
    creds_path: str | None = None
    secrets: tuple[SecretInput, ...] = ()
    tls: TlsConfig = msgspec.field(default_factory=TlsConfig)


class ProbeResponse(msgspec.Struct, frozen=True):
    client: ProbeResult
    monitoring: ProbeResult


def derive_monitoring_url(client_url: str) -> str | None:
    """The form's auto-derivation: same host, http, port 8222.

    Deliberately dumb -- it is a suggestion the user can edit, and the probe is
    what decides whether it was right.
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(client_url if "://" in client_url else f"nats://{client_url}")
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return f"http://{parts.hostname}:8222"
