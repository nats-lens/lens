"""JetStream: streams, consumers, per-subject counts, stored messages.

FROZEN CONTRACT -- see domain/common.py. Every field here is `jetstream`
provenance: it comes from the JetStream API over the client connection, which a
plain nats-py client genuinely can reach.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

import msgspec

from nats_lens.codec.schemas import Decoded


class Retention(StrEnum):
    LIMITS = "limits"
    INTEREST = "interest"
    WORKQUEUE = "workqueue"


class Storage(StrEnum):
    FILE = "file"
    MEMORY = "memory"


class Discard(StrEnum):
    OLD = "old"
    NEW = "new"


class PeerInfo(msgspec.Struct, frozen=True):
    name: str
    current: bool
    offline: bool
    active_seconds: float
    lag: int = 0
    is_leader: bool = False


class ClusterInfo(msgspec.Struct, frozen=True):
    name: str | None
    leader: str | None
    replicas: tuple[PeerInfo, ...] = ()


class StreamLimits(msgspec.Struct, frozen=True):
    max_consumers: int
    max_msgs: int
    max_bytes: int
    max_age_seconds: float
    max_msg_size: int
    max_msgs_per_subject: int
    duplicate_window_seconds: float
    discard: Discard
    allow_direct: bool = False
    allow_rollup: bool = False
    deny_delete: bool = False
    deny_purge: bool = False


class StreamState(msgspec.Struct, frozen=True):
    messages: int
    bytes: int
    first_seq: int
    last_seq: int
    consumer_count: int
    num_deleted: int = 0
    num_subjects: int = 0


class StreamSummary(msgspec.Struct, frozen=True):
    """A row in the stream list."""

    name: str
    subjects: tuple[str, ...]
    storage: Storage
    retention: Retention
    replicas: int
    description: str | None
    state: StreamState
    usage: float | None
    """Fraction of whichever limit fills first, 0 to 1. None when none is set."""
    """Against max_bytes or max_msgs, whichever binds first. None when unlimited."""
    leader: str | None


class StreamDetail(msgspec.Struct, frozen=True):
    name: str
    subjects: tuple[str, ...]
    description: str | None
    storage: Storage
    retention: Retention
    replicas: int
    limits: StreamLimits
    state: StreamState
    cluster: ClusterInfo
    created: datetime
    mirror: str | None = None
    sources: tuple[str, ...] = ()
    republish_to: str | None = None
    placement_tags: tuple[str, ...] = ()
    sealed: bool = False


class AckPolicy(StrEnum):
    NONE = "none"
    ALL = "all"
    EXPLICIT = "explicit"


class DeliverPolicy(StrEnum):
    ALL = "all"
    LAST = "last"
    NEW = "new"
    BY_START_SEQUENCE = "by_start_sequence"
    BY_START_TIME = "by_start_time"
    LAST_PER_SUBJECT = "last_per_subject"


class ConsumerHealth(StrEnum):
    """How the design colours a consumer row."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILING = "failing"


class ConsumerSummary(msgspec.Struct, frozen=True):
    """A consumer row, with the three numbers that actually diagnose it."""

    name: str
    stream: str
    durable: bool
    push: bool
    filter_subjects: tuple[str, ...]
    ack_policy: AckPolicy
    deliver_policy: DeliverPolicy
    ack_wait_seconds: float
    max_deliver: int
    num_pending: int
    """Lag: stream.last_seq minus this consumer's delivered.stream_seq."""
    num_ack_pending: int
    max_ack_pending: int
    """The configured ceiling. Without it `num_ack_pending` has no scale."""
    num_redelivered: int
    delivered_stream_seq: int
    delivered_consumer_seq: int
    lag: float | None
    """Fraction of the messages this consumer's filter selects that it has not
    been delivered, 0 to 1. None when the filtered total is unknowable."""
    health: ConsumerHealth
    leader: str | None = None
    description: str | None = None
    deliver_group: str | None = None
    """Set when several push instances share the load through a queue group."""
    backoff_seconds: tuple[float, ...] = ()
    paused: bool = False
    paused_until: datetime | None = None
    num_waiting: int = 0
    num_redelivered_total: int = 0


class SubjectCount(msgspec.Struct, frozen=True):
    """Server-side per-subject totals.

    From `jsm.stream_info(name, subjects_filter=...)` -> `state.subjects`. Note the
    singular call: `streams_info` (plural) takes only an offset and cannot do this.
    """

    subject: str
    count: int
    share_of_largest: float
    """This subject's count as a fraction of the busiest subject's, 0 to 1.

    A fraction, like every other proportion in the API: `Meter` and `percent()`
    both take one, and a field spelling `pct` while holding a percentage is what
    put a 10000% lag on screen."""


class StoredMessage(msgspec.Struct, frozen=True):
    seq: int
    subject: str
    time: datetime
    size: int
    headers: dict[str, str]
    payload_b64: str
    decoded: Decoded


class MessageQuery(msgspec.Struct, frozen=True):
    """One page of stored messages, read by sequence, by subject, or by both."""

    seq: int | None = None
    """Where to start. Means the same with or without a subject, which is what
    lets a caller page: ask again from the sequence after the last row."""
    subject: str | None = None
    direct: bool = False
    """Only honoured when the stream was created with `allow_direct`; the server
    decides, so callers can always ask."""
    limit: int = 20
    """Page size, not a session budget."""


class StreamUpdate(msgspec.Struct, frozen=True, omit_defaults=True):
    """What a live stream will accept a change to.

    A stream's name, storage backend and retention policy are fixed at creation:
    changing them would mean a different stream holding the same messages, so the
    server refuses and they are not offered here. Everything else can be tuned in
    place, which is the difference between editing a stream and re-creating it
    without its messages.
    """

    subjects: tuple[str, ...] | None = None
    description: str | None = None
    max_age_seconds: float | None = None
    max_msgs: int | None = None
    max_bytes: int | None = None
    max_msg_size: int | None = None
    max_msgs_per_subject: int | None = None
    max_consumers: int | None = None
    discard: Discard | None = None
    duplicate_window_seconds: float | None = None
    replicas: int | None = None
    allow_rollup: bool | None = None
    deny_delete: bool | None = None
    deny_purge: bool | None = None
    republish_source: str | None = None
    republish_destination: str | None = None
    metadata: dict[str, str] | None = None


class StreamCreate(msgspec.Struct, frozen=True):
    name: str
    subjects: tuple[str, ...]
    storage: Storage = Storage.FILE
    retention: Retention = Retention.LIMITS
    replicas: int = 1
    description: str | None = None
    max_age_seconds: float = 0.0
    max_msgs: int = -1
    max_bytes: int = -1
    max_msg_size: int = -1
    discard: Discard = Discard.OLD
    duplicate_window_seconds: float = 120.0


class ReplayPolicy(StrEnum):
    INSTANT = "instant"
    ORIGINAL = "original"
    """Redeliver at the original spacing. Useful for replaying a recorded load."""


class ConsumerCreate(msgspec.Struct, frozen=True):
    stream: str
    name: str
    durable: bool = True
    push: bool = False
    deliver_subject: str | None = None
    deliver_group: str | None = None
    """The queue group for a push consumer -- how several instances share the load.

    Push-only: a pull consumer balances by competing on fetch, so it needs none.
    """
    filter_subjects: tuple[str, ...] = ()
    ack_policy: AckPolicy = AckPolicy.EXPLICIT
    deliver_policy: DeliverPolicy = DeliverPolicy.ALL
    opt_start_seq: int | None = None
    """Required by `by_start_sequence`, and meaningless without it."""
    opt_start_time: datetime | None = None
    """Required by `by_start_time`, and meaningless without it."""
    replay_policy: ReplayPolicy = ReplayPolicy.INSTANT
    ack_wait_seconds: float = 30.0
    max_deliver: int = -1
    backoff_seconds: tuple[float, ...] = ()
    """Delay before each redelivery, in order.

    The answer to a redelivery storm: without it every retry is `ack_wait` apart,
    so a handler failing on a poison message hammers it at a fixed rate until
    `max_deliver` gives up. Only meaningful with an explicit ack policy.

    Note that the server overwrites `ack_wait` with the first delay when this is
    set -- so a consumer created with backoff `[2, 10, 30]` reports `ack_wait` of
    2 regardless of what was asked for. That is NATS behaviour, not a rounding
    error, and the UI should say so rather than look like it lost the value.
    """
    max_ack_pending: int = 1000
    max_waiting: int = 512
    """Pull only: how many fetch requests may be parked at once."""
    idle_heartbeat_seconds: float = 0.0
    """Push only: how often to send an empty frame so a silent link is noticed."""
    flow_control: bool = False
    """Push only: let the server pace delivery to what the client is keeping up with."""
    rate_limit_bps: int = 0
    headers_only: bool = False
    sample_freq: str | None = None
    """Percentage of acks to report as advisories, e.g. `"100"`."""
    inactive_threshold_seconds: float = 0.0
    """How long an ephemeral consumer survives with nobody attached."""
    num_replicas: int = 0
    """0 means inherit the stream's."""
    mem_storage: bool = False
    metadata: dict[str, str] = {}
    description: str | None = None


class ConsumerUpdate(msgspec.Struct, frozen=True, omit_defaults=True):
    """The fields a running consumer will accept a change to.

    NATS updates a consumer by re-adding it under the same durable name, and
    refuses the fields that would change its identity or its position in the
    stream -- deliver policy, ack policy, whether it is push or pull. Those are
    absent here rather than sent and rejected.
    """

    description: str | None = None
    ack_wait_seconds: float | None = None
    max_deliver: int | None = None
    backoff_seconds: tuple[float, ...] | None = None
    max_ack_pending: int | None = None
    max_waiting: int | None = None
    filter_subjects: tuple[str, ...] | None = None
    rate_limit_bps: int | None = None
    headers_only: bool | None = None
    sample_freq: str | None = None
    inactive_threshold_seconds: float | None = None
    metadata: dict[str, str] | None = None


class ConsumerPauseRequest(msgspec.Struct, frozen=True):
    """Pause a consumer until a moment.

    There is no indefinite pause in the protocol: NATS pauses *until* a
    timestamp, and resuming is literally pausing until the epoch. So omitting
    `until` does not mean "forever" -- it means the default below, and the
    response reports the real deadline rather than implying there is none.
    """

    until: datetime | None = None
    default_days: int = 365
    """Used when `until` is omitted. Long, but a real date the UI can show."""


class PurgeRequest(msgspec.Struct, frozen=True):
    subject: str | None = None
    keep: int | None = None
    up_to_seq: int | None = None
