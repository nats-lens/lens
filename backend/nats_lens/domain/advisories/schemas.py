"""JetStream advisories and $SYS account events.

FROZEN CONTRACT -- see domain/common.py.

These are transient: published once, never stored. The feed starts empty on every
reconnect and the UI says so. Provenance is `system` (a $SYS connection) for
account events and `jetstream` for $JS.EVENT.ADVISORY.>.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

import msgspec


class AdvisoryKind(StrEnum):
    MAX_DELIVERIES = "max_deliveries"
    NAK = "nak"
    TERMINATED = "terminated"
    ACK = "ack"
    CONSUMER_ACTION = "consumer_action"
    STREAM_ACTION = "stream_action"
    LEADER_ELECTED = "leader_elected"
    QUORUM_LOST = "quorum_lost"
    CLIENT_CONNECT = "client_connect"
    CLIENT_DISCONNECT = "client_disconnect"
    SERVER_STATSZ = "server_statsz"
    OTHER = "other"


class Severity(StrEnum):
    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    ALERT = "alert"


class AdvisoryEvent(msgspec.Struct, frozen=True):
    id: str
    at: datetime
    kind: AdvisoryKind
    severity: Severity
    type_url: str
    """`io.nats.jetstream.advisory.v1.max_deliver`, from the advisory's own `type`."""
    subject: str
    target: str
    """`ORDERS - search-index`, assembled for the list row."""
    summary: str
    body: str
    """The raw advisory JSON, pretty-printed. What the server actually sent."""
    explanation: str
    """What it means and what usually causes it. Written by nats-lens, not the server."""
    actions: tuple[AdvisoryAction, ...] = ()


class AdvisoryAction(msgspec.Struct, frozen=True):
    """A suggested next step the UI renders as a button."""

    label: str
    kind: str
    """`open_consumer` | `open_stream` | `republish` | `open_connection` | `edit_server`."""
    target: str


class AdvisoryTypeCount(msgspec.Struct, frozen=True):
    kind: AdvisoryKind
    label: str
    count: int
    severity: Severity


class AdvisoryFeedState(msgspec.Struct, frozen=True):
    """Whether anything is being kept, and the honest default: nothing is."""

    listening: bool
    started_at: datetime | None
    seen: int
    """`sampled` -- events observed since this feed started, not a server total."""
    buffer_size: int
    capture_stream: str | None = None
    """When set, advisories are also being written to this durable stream."""
    note: str = (
        "Advisories are published once and never stored. This feed starts empty on "
        "every reconnect and holds only what nats-lens has seen since."
    )


class CaptureStreamRequest(msgspec.Struct, frozen=True):
    """Make advisories durable by having JetStream keep them."""

    name: str = "ADVISORIES"
    subjects: tuple[str, ...] = ("$JS.EVENT.ADVISORY.>",)
    max_age_seconds: float = 604800.0
    max_msgs: int = 1_000_000
    replicas: int = 1
