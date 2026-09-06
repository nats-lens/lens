"""Streams, consumers, per-subject counts and stored messages.

Every value here is `jetstream` provenance -- the frozen contract says so in its
module docstring -- so there is no `Sourced[...]` wrapper to fill in. The honesty
rule is enforced differently: when JetStream is not reachable the whole request
fails with the same curated reason `Sourced.missing` would have carried, rather
than the caller getting an empty list that looks like an empty stream.

Connecting, checking that JetStream is actually reachable and translating
nats-py exceptions is `domain/nats_access.py`'s job -- shared with the KV and
object-store services, which are the same account seen from a different angle
and fail in exactly the same ways.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from litestar.di import NamedDependency
from litestar.exceptions import ClientException
from nats.js import api
from nats.js import errors as js_errors
from nats.js.manager import JetStreamManager
from sqlalchemy.ext.asyncio import AsyncSession

from nats_lens.codec.rules import pattern_matches
from nats_lens.conn.manager import ConnectionManager
from nats_lens.domain.jetstream.schemas import (
    AckPolicy,
    ClusterInfo,
    ConsumerCreate,
    ConsumerHealth,
    ConsumerPauseRequest,
    ConsumerSummary,
    ConsumerUpdate,
    DeliverPolicy,
    Discard,
    MessageQuery,
    PeerInfo,
    PurgeRequest,
    Retention,
    Storage,
    StoredMessage,
    StreamCreate,
    StreamDetail,
    StreamLimits,
    StreamState,
    StreamSummary,
    StreamUpdate,
    SubjectCount,
)
from nats_lens.domain.nats_access import (
    PAGE_SIZE,
    decode_payload,
    enum_value,
    iter_stream_infos,
    jetstream,
)

_MAX_SCAN_MULTIPLE = 50
"""Bound on how many sequence numbers `read_messages` will skip over looking for
a match, so a stream with large gaps (heavy deletes, a narrow subject filter)
cannot turn one request into an unbounded scan of the whole stream."""

# ---------------------------------------------------------------- stream mapping


def _usage(state: StreamState, limits: StreamLimits) -> float | None:
    """Fraction of whichever of max_bytes / max_msgs fills up first, 0 to 1.

    None when neither is bounded -- there is nothing for a fraction to be a
    fraction of, and 0 would read as an empty stream."""
    ratios = []
    if limits.max_bytes > 0:
        ratios.append(state.bytes / limits.max_bytes)
    if limits.max_msgs > 0:
        ratios.append(state.messages / limits.max_msgs)
    if not ratios:
        return None
    return round(min(1.0, max(ratios)), 4)


def _stream_state(state: api.StreamState) -> StreamState:
    return StreamState(
        messages=state.messages,
        bytes=state.bytes,
        first_seq=state.first_seq,
        last_seq=state.last_seq,
        consumer_count=state.consumer_count,
        num_deleted=state.num_deleted or 0,
        num_subjects=len(state.subjects) if state.subjects else 0,
    )


def _stream_limits(config: api.StreamConfig) -> StreamLimits:
    return StreamLimits(
        max_consumers=config.max_consumers if config.max_consumers is not None else -1,
        max_msgs=config.max_msgs if config.max_msgs is not None else -1,
        max_bytes=config.max_bytes if config.max_bytes is not None else -1,
        max_age_seconds=config.max_age or 0.0,
        max_msg_size=config.max_msg_size if config.max_msg_size is not None else -1,
        max_msgs_per_subject=config.max_msgs_per_subject,
        duplicate_window_seconds=config.duplicate_window or 0.0,
        discard=Discard(enum_value(config.discard, "old")),
        allow_direct=bool(config.allow_direct),
        allow_rollup=bool(config.allow_rollup_hdrs),
        deny_delete=bool(config.deny_delete),
        deny_purge=bool(config.deny_purge),
    )


def stream_summary(info: api.StreamInfo) -> StreamSummary:
    config = info.config
    state = _stream_state(info.state)
    limits = _stream_limits(config)
    return StreamSummary(
        name=config.name or "",
        subjects=tuple(config.subjects or ()),
        storage=Storage(enum_value(config.storage, "file")),
        retention=Retention(enum_value(config.retention, "limits")),
        replicas=config.num_replicas or 1,
        description=config.description,
        state=state,
        usage=_usage(state, limits),
        leader=info.cluster.leader if info.cluster else None,
    )


def _cluster_info(cluster: api.ClusterInfo | None) -> ClusterInfo:
    if cluster is None:
        return ClusterInfo(name=None, leader=None, replicas=())
    return ClusterInfo(
        name=cluster.name,
        leader=cluster.leader,
        replicas=tuple(
            PeerInfo(
                name=peer.name or "",
                current=bool(peer.current),
                offline=bool(peer.offline),
                # `active` is nanoseconds on the wire, like every other duration in
                # this API; `api.PeerInfo` is the one place `from_response` does not
                # convert it, so it happens here instead.
                active_seconds=(peer.active or 0) / 1_000_000_000,
                lag=peer.lag or 0,
                is_leader=peer.name is not None and peer.name == cluster.leader,
            )
            for peer in (cluster.replicas or ())
        ),
    )


def stream_detail(info: api.StreamInfo) -> StreamDetail:
    config = info.config
    return StreamDetail(
        name=config.name or "",
        subjects=tuple(config.subjects or ()),
        description=config.description,
        storage=Storage(enum_value(config.storage, "file")),
        retention=Retention(enum_value(config.retention, "limits")),
        replicas=config.num_replicas or 1,
        limits=_stream_limits(config),
        state=_stream_state(info.state),
        cluster=_cluster_info(info.cluster),
        created=info.created or datetime.now(UTC),
        mirror=config.mirror.name if config.mirror else None,
        sources=tuple(s.name for s in (config.sources or ())),
        republish_to=config.republish.dest if config.republish else None,
        placement_tags=tuple(config.placement.tags or ()) if config.placement else (),
        sealed=bool(config.sealed),
    )


# -------------------------------------------------------------- consumer mapping


def _paused_until(info: api.ConsumerInfo) -> datetime | None:
    """When a paused consumer resumes.

    The server reports `pause_remaining` in nanoseconds, not a timestamp, so the
    deadline has to be reconstructed. Older servers have no pause at all, hence
    the defensive read rather than an attribute access.
    """
    remaining = getattr(info, "pause_remaining", None)
    if not remaining or not isinstance(remaining, int) or remaining <= 0:
        return None
    return datetime.now(UTC) + timedelta(microseconds=remaining / 1_000)


def _matching_messages(
    filters: tuple[str, ...], subject_counts: Mapping[str, int] | None
) -> int | None:
    """How many messages in the stream this consumer's filter actually selects.

    None when the stream did not report its per-subject counts -- NATS caps that
    list, and a guess here would be exactly the kind of confident wrong number
    this tool exists to avoid.
    """
    if subject_counts is None:
        return None
    if not filters:
        return sum(subject_counts.values())
    return sum(
        count
        for subject, count in subject_counts.items()
        if any(pattern_matches(f, subject) for f in filters)
    )


def _lag(num_pending: int, matching_messages: int | None) -> float | None:
    """What fraction of the messages this consumer *cares about* is undelivered.

    The denominator has to be the filtered count, not the stream's. `num_pending`
    only counts messages matching the consumer's filter, so dividing it by the
    whole stream understates the lag by exactly the ratio the filter excludes: a
    consumer on `orders.new` that has read nothing of its 1.7k messages reported
    3.6% against a 48k stream, which reads as almost caught up when it has not
    started.

    None rather than a number when the filtered total is unknown, and None rather
    than zero when the filter selects nothing -- there is no percentage of nothing.
    """
    if matching_messages is None or matching_messages <= 0:
        return None
    return round(min(1.0, max(0.0, num_pending / matching_messages)), 4)


def _consumer_health(
    num_pending: int, num_ack_pending: int, num_redelivered: int, max_ack_pending: int
) -> ConsumerHealth:
    """A consumer whose acks are piling up against its own limit, or that is
    being redelivered more than it is receiving new messages, is stuck or
    crash-looping -- that is `failing`, not merely behind. Short of that,
    any ack backlog or redelivery at all is worth a second look (`degraded`);
    a clean, caught-up consumer is `healthy`.
    """
    ack_pending_pct = (num_ack_pending / max_ack_pending * 100) if max_ack_pending > 0 else 0.0
    if ack_pending_pct >= 90 or (num_redelivered > 0 and num_redelivered >= max(num_pending, 1)):
        return ConsumerHealth.FAILING
    if ack_pending_pct >= 50 or num_redelivered > 0:
        return ConsumerHealth.DEGRADED
    return ConsumerHealth.HEALTHY


def _filter_subjects(config: api.ConsumerConfig) -> tuple[str, ...]:
    if config.filter_subjects:
        return tuple(config.filter_subjects)
    if config.filter_subject and config.filter_subject != ">":
        return (config.filter_subject,)
    return ()


def consumer_summary(
    info: api.ConsumerInfo, subject_counts: Mapping[str, int] | None
) -> ConsumerSummary:
    config = info.config
    delivered = info.delivered
    num_pending = info.num_pending or 0
    num_ack_pending = info.num_ack_pending or 0
    num_redelivered = info.num_redelivered or 0
    max_ack_pending = config.max_ack_pending or 0
    filters = _filter_subjects(config)
    lag = _lag(num_pending, _matching_messages(filters, subject_counts))
    return ConsumerSummary(
        name=info.name,
        stream=info.stream_name,
        durable=bool(config.durable_name),
        push=bool(config.deliver_subject),
        filter_subjects=filters,
        ack_policy=AckPolicy(enum_value(config.ack_policy, "explicit")),
        deliver_policy=DeliverPolicy(enum_value(config.deliver_policy, "all")),
        ack_wait_seconds=config.ack_wait or 0.0,
        max_deliver=config.max_deliver if config.max_deliver is not None else -1,
        num_pending=num_pending,
        num_ack_pending=num_ack_pending,
        max_ack_pending=max_ack_pending,
        num_redelivered=num_redelivered,
        delivered_stream_seq=delivered.stream_seq if delivered else 0,
        delivered_consumer_seq=delivered.consumer_seq if delivered else 0,
        lag=lag,
        health=_consumer_health(num_pending, num_ack_pending, num_redelivered, max_ack_pending),
        leader=info.cluster.leader if info.cluster else None,
        description=config.description,
        deliver_group=config.deliver_group,
        backoff_seconds=tuple(config.backoff or ()),
        # Read from the info rather than the config: a pause whose deadline has
        # passed reports as not paused, which is the truth on the wire.
        paused=bool(getattr(info, "paused", False)),
        paused_until=_paused_until(info),
        num_waiting=info.num_waiting or 0,
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _validate_consumer(data: ConsumerCreate) -> None:
    """Refuse the combinations NATS would refuse, with a sentence that explains.

    The server's own errors here are terse, and two of these are easy to get
    wrong from a form: a start policy with nothing to start from, and a queue
    group on a consumer that has no delivery subject to group over.
    """
    if data.push and not data.deliver_subject:
        raise ClientException(detail="A push consumer needs a deliver_subject to push messages to.")
    if data.deliver_group and not data.push:
        raise ClientException(
            detail=(
                "A queue group only applies to a push consumer. Pull consumers already "
                "share work by competing for the same fetches."
            )
        )
    if data.deliver_policy is DeliverPolicy.BY_START_SEQUENCE and data.opt_start_seq is None:
        raise ClientException(
            detail="deliver_policy by_start_sequence needs opt_start_seq to say which sequence."
        )
    if data.deliver_policy is DeliverPolicy.BY_START_TIME and data.opt_start_time is None:
        raise ClientException(
            detail="deliver_policy by_start_time needs opt_start_time to say from when."
        )
    if data.backoff_seconds and data.ack_policy is not AckPolicy.EXPLICIT:
        raise ClientException(
            detail=(
                "backoff only means something with an explicit ack policy; "
                "nothing is redelivered otherwise."
            )
        )
    if data.backoff_seconds and 0 < data.max_deliver < len(data.backoff_seconds) + 1:
        raise ClientException(
            detail=(
                f"{len(data.backoff_seconds)} backoff delays need max_deliver of at least "
                f"{len(data.backoff_seconds) + 1}; it is {data.max_deliver}."
            )
        )


def _apply_filter_subjects(config: api.ConsumerConfig, filters: tuple[str, ...]) -> None:
    """`filter_subject` and `filter_subjects` are mutually exclusive on the wire;
    the server rejects a request that sets both, so exactly one gets set here."""
    if len(filters) == 1:
        config.filter_subject = filters[0]
    elif len(filters) > 1:
        config.filter_subjects = list(filters)


# ---------------------------------------------------------------------- service


class JetStreamService:
    """Everything behind `/api/servers/{id}/jetstream`."""

    def __init__(self, connections: ConnectionManager, session: AsyncSession) -> None:
        self._connections = connections
        self._session = session

    # ----------------------------------------------------------------- streams

    async def list_streams(self, server_id: uuid.UUID) -> list[StreamSummary]:
        instance = f"/api/servers/{server_id}/jetstream/streams"
        async with jetstream(self._connections, server_id, instance) as js:
            return [stream_summary(info) async for info in iter_stream_infos(js.manager)]

    async def create_stream(self, server_id: uuid.UUID, data: StreamCreate) -> StreamDetail:
        instance = f"/api/servers/{server_id}/jetstream/streams"
        async with jetstream(self._connections, server_id, instance) as js:
            config = api.StreamConfig(
                name=data.name,
                description=data.description,
                subjects=list(data.subjects),
                storage=api.StorageType(data.storage.value),
                retention=api.RetentionPolicy(data.retention.value),
                num_replicas=data.replicas,
                max_age=data.max_age_seconds,
                max_msgs=data.max_msgs,
                max_bytes=data.max_bytes,
                max_msg_size=data.max_msg_size,
                discard=api.DiscardPolicy(data.discard.value),
                duplicate_window=data.duplicate_window_seconds,
            )
            info = await js.manager.add_stream(config)
            return stream_detail(info)

    async def get_stream(self, server_id: uuid.UUID, name: str) -> StreamDetail:
        instance = f"/api/servers/{server_id}/jetstream/streams/{name}"
        async with jetstream(self._connections, server_id, instance) as js:
            return stream_detail(await js.manager.stream_info(name))

    async def delete_stream(self, server_id: uuid.UUID, name: str) -> None:
        instance = f"/api/servers/{server_id}/jetstream/streams/{name}"
        async with jetstream(self._connections, server_id, instance) as js:
            await js.manager.delete_stream(name)

    async def purge_stream(
        self, server_id: uuid.UUID, name: str, data: PurgeRequest
    ) -> StreamDetail:
        instance = f"/api/servers/{server_id}/jetstream/streams/{name}/purge"
        async with jetstream(self._connections, server_id, instance) as js:
            await js.manager.purge_stream(
                name, seq=data.up_to_seq, subject=data.subject, keep=data.keep
            )
            return stream_detail(await js.manager.stream_info(name))

    async def stream_subjects(
        self, server_id: uuid.UUID, name: str, filter: str
    ) -> list[SubjectCount]:
        """`stream_info(name, subjects_filter=...)` -- the singular call. The
        plural `streams_info` takes only an offset and cannot do this."""
        instance = f"/api/servers/{server_id}/jetstream/streams/{name}/subjects"
        async with jetstream(self._connections, server_id, instance) as js:
            info = await js.manager.stream_info(name, subjects_filter=filter or ">")
            subjects = info.state.subjects or {}
            if not subjects:
                return []
            largest = max(subjects.values()) or 1
            ordered = sorted(subjects.items(), key=lambda item: item[1], reverse=True)
            return [
                SubjectCount(
                    subject=subject, count=count, share_of_largest=round(count / largest, 4)
                )
                for subject, count in ordered
            ]

    # --------------------------------------------------------------- consumers

    async def _all_consumers(self, jsm: JetStreamManager, stream: str) -> list[api.ConsumerInfo]:
        consumers: list[api.ConsumerInfo] = []
        offset = 0
        while True:
            batch = await jsm.consumers_info(stream, offset=offset)
            consumers.extend(batch)
            if len(batch) < PAGE_SIZE:
                return consumers
            offset += len(batch)

    async def list_consumers(self, server_id: uuid.UUID, name: str) -> list[ConsumerSummary]:
        instance = f"/api/servers/{server_id}/jetstream/streams/{name}/consumers"
        async with jetstream(self._connections, server_id, instance) as js:
            # `subjects_filter` is what makes the server report per-subject
            # counts, and those are the only honest denominator for a filtered
            # consumer's lag. One call for the whole list, not one per consumer.
            stream = await js.manager.stream_info(name, subjects_filter=">")
            consumers = await self._all_consumers(js.manager, name)
            counts = stream.state.subjects
            return [consumer_summary(info, counts) for info in consumers]

    async def create_consumer(
        self, server_id: uuid.UUID, name: str, data: ConsumerCreate
    ) -> ConsumerSummary:
        instance = f"/api/servers/{server_id}/jetstream/streams/{name}/consumers"
        async with jetstream(self._connections, server_id, instance) as js:
            _validate_consumer(data)
            config = api.ConsumerConfig(
                name=data.name,
                durable_name=data.name if data.durable else None,
                description=data.description,
                deliver_subject=data.deliver_subject if data.push else None,
                # A queue group only exists for push: a pull consumer shares work
                # by competing on fetch, and the server rejects the pairing.
                deliver_group=data.deliver_group if data.push else None,
                ack_policy=api.AckPolicy(data.ack_policy.value),
                deliver_policy=api.DeliverPolicy(data.deliver_policy.value),
                replay_policy=api.ReplayPolicy(data.replay_policy.value),
                opt_start_seq=data.opt_start_seq,
                opt_start_time=data.opt_start_time,
                ack_wait=data.ack_wait_seconds,
                max_deliver=data.max_deliver,
                backoff=list(data.backoff_seconds) or None,
                max_ack_pending=data.max_ack_pending,
                max_waiting=data.max_waiting if not data.push else None,
                idle_heartbeat=data.idle_heartbeat_seconds or None,
                flow_control=data.flow_control if data.push else False,
                rate_limit_bps=data.rate_limit_bps or None,
                headers_only=data.headers_only or None,
                sample_freq=data.sample_freq,
                inactive_threshold=data.inactive_threshold_seconds or None,
                num_replicas=data.num_replicas or None,
                mem_storage=data.mem_storage or None,
                metadata=dict(data.metadata) or None,
            )
            _apply_filter_subjects(config, data.filter_subjects)
            info = await js.manager.add_consumer(name, config)
            stream = await js.manager.stream_info(name, subjects_filter=">")
            return consumer_summary(info, stream.state.subjects)

    async def update_stream(
        self, server_id: uuid.UUID, name: str, data: StreamUpdate
    ) -> StreamDetail:
        """Change a live stream in place, keeping its messages.

        Only the fields actually supplied are touched: the config is read back
        first and patched, because `update_stream` replaces the whole thing and
        omitting a field would silently reset it to its default.

        Name, storage and retention are not in `StreamUpdate` at all -- the server
        refuses them, and a form that offered them would only produce errors.
        """
        instance = f"/api/servers/{server_id}/jetstream/streams/{name}"
        async with jetstream(self._connections, server_id, instance) as js:
            config = (await js.manager.stream_info(name)).config

            if data.subjects is not None:
                config.subjects = list(data.subjects)
            if data.description is not None:
                config.description = data.description
            if data.max_age_seconds is not None:
                config.max_age = data.max_age_seconds
            if data.max_msgs is not None:
                config.max_msgs = data.max_msgs
            if data.max_bytes is not None:
                config.max_bytes = data.max_bytes
            if data.max_msg_size is not None:
                config.max_msg_size = data.max_msg_size
            if data.max_msgs_per_subject is not None:
                config.max_msgs_per_subject = data.max_msgs_per_subject
            if data.max_consumers is not None:
                config.max_consumers = data.max_consumers
            if data.discard is not None:
                config.discard = api.DiscardPolicy(data.discard.value)
            if data.duplicate_window_seconds is not None:
                config.duplicate_window = data.duplicate_window_seconds
            if data.replicas is not None:
                config.num_replicas = data.replicas
            if data.allow_rollup is not None:
                config.allow_rollup_hdrs = data.allow_rollup
            if data.deny_delete is not None:
                config.deny_delete = data.deny_delete
            if data.deny_purge is not None:
                config.deny_purge = data.deny_purge
            if data.metadata is not None:
                config.metadata = dict(data.metadata)
            if data.republish_destination is not None:
                config.republish = api.RePublish(
                    src=data.republish_source or ">", dest=data.republish_destination
                )

            info = await js.manager.update_stream(config)
            return stream_detail(info)

    async def update_consumer(
        self, server_id: uuid.UUID, name: str, consumer: str, data: ConsumerUpdate
    ) -> ConsumerSummary:
        """Change a running consumer.

        NATS has no separate update call: re-adding under the same durable name
        is the update. So the existing config is read, patched and re-sent -- and
        the server rejects anything that would change the consumer's identity,
        which is why `ConsumerUpdate` does not offer those fields.
        """
        instance = f"/api/servers/{server_id}/jetstream/streams/{name}/consumers/{consumer}"
        async with jetstream(self._connections, server_id, instance) as js:
            config = (await js.manager.consumer_info(name, consumer)).config

            if data.description is not None:
                config.description = data.description
            if data.ack_wait_seconds is not None:
                config.ack_wait = data.ack_wait_seconds
            if data.max_deliver is not None:
                config.max_deliver = data.max_deliver
            if data.backoff_seconds is not None:
                config.backoff = list(data.backoff_seconds) or None
            if data.max_ack_pending is not None:
                config.max_ack_pending = data.max_ack_pending
            if data.max_waiting is not None:
                config.max_waiting = data.max_waiting
            if data.rate_limit_bps is not None:
                config.rate_limit_bps = data.rate_limit_bps or None
            if data.headers_only is not None:
                config.headers_only = data.headers_only
            if data.sample_freq is not None:
                config.sample_freq = data.sample_freq
            if data.inactive_threshold_seconds is not None:
                config.inactive_threshold = data.inactive_threshold_seconds or None
            if data.metadata is not None:
                config.metadata = dict(data.metadata)
            if data.filter_subjects is not None:
                _apply_filter_subjects(config, data.filter_subjects)

            info = await js.manager.add_consumer(name, config)
            stream = await js.manager.stream_info(name, subjects_filter=">")
            return consumer_summary(info, stream.state.subjects)

    async def pause_consumer(
        self, server_id: uuid.UUID, name: str, consumer: str, data: ConsumerPauseRequest
    ) -> ConsumerSummary:
        """Stop delivering to a consumer without deleting it.

        The answer to a consumer in a redelivery loop: its position and durable
        state survive, and resuming picks up where it left off.

        NATS pauses until a timestamp -- there is no open-ended pause -- so when
        no deadline is given one is chosen and reported back, rather than
        implying the pause has none.
        """
        instance = f"/api/servers/{server_id}/jetstream/streams/{name}/consumers/{consumer}/pause"
        until = data.until or (_now() + timedelta(days=data.default_days))
        async with jetstream(self._connections, server_id, instance) as js:
            await js.manager.pause_consumer(
                name, consumer, until.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            )
            return await self.get_consumer(server_id, name, consumer)

    async def resume_consumer(
        self, server_id: uuid.UUID, name: str, consumer: str
    ) -> ConsumerSummary:
        instance = f"/api/servers/{server_id}/jetstream/streams/{name}/consumers/{consumer}/resume"
        async with jetstream(self._connections, server_id, instance) as js:
            await js.manager.resume_consumer(name, consumer)
            return await self.get_consumer(server_id, name, consumer)

    async def get_consumer(self, server_id: uuid.UUID, name: str, consumer: str) -> ConsumerSummary:
        instance = f"/api/servers/{server_id}/jetstream/streams/{name}/consumers/{consumer}"
        async with jetstream(self._connections, server_id, instance) as js:
            info = await js.manager.consumer_info(name, consumer)
            stream = await js.manager.stream_info(name, subjects_filter=">")
            return consumer_summary(info, stream.state.subjects)

    async def delete_consumer(self, server_id: uuid.UUID, name: str, consumer: str) -> None:
        instance = f"/api/servers/{server_id}/jetstream/streams/{name}/consumers/{consumer}"
        async with jetstream(self._connections, server_id, instance) as js:
            await js.manager.delete_consumer(name, consumer)

    # ---------------------------------------------------------------- messages

    async def _stored_message(self, server_id: uuid.UUID, raw: api.RawStreamMsg) -> StoredMessage:
        payload = raw.data or b""
        headers = dict(raw.headers or {})
        subject = raw.subject or ""
        decoded = await decode_payload(self._session, server_id, payload, subject, headers)
        return StoredMessage(
            seq=raw.seq or 0,
            subject=raw.subject or "",
            time=raw.time or datetime.now(UTC),
            size=len(payload),
            headers=headers,
            payload_b64=base64.b64encode(payload).decode(),
            decoded=decoded,
        )

    async def read_messages(
        self, server_id: uuid.UUID, name: str, query: MessageQuery
    ) -> list[StoredMessage]:
        """By sequence, by subject, or both.

        Both walk forward collecting up to `query.limit` messages, and `seq`
        means the same thing in either: start here. A plain sequence advances one
        at a time, skipping any that were deleted; a subject asks the server for
        the next match at or after the cursor.

        That `seq` is the starting point *whichever* mode is in play is what
        makes paging work. It used to be ignored as soon as a subject was given,
        so asking for the page after sequence 400 on `orders.new` returned the
        first page again, for ever.
        """
        instance = f"/api/servers/{server_id}/jetstream/streams/{name}/messages"
        async with jetstream(self._connections, server_id, instance) as js:
            jsm = js.manager
            info = await jsm.stream_info(name)
            direct = query.direct and bool(info.config.allow_direct)
            limit = max(1, min(query.limit, 200))

            subject = query.subject
            start = query.seq or info.state.first_seq or 1
            # `next` mode is what filters by subject server-side; without one the
            # walk is a plain sequence scan.
            cursor, use_next = start, subject is not None

            messages: list[StoredMessage] = []
            last_seq = info.state.last_seq
            max_scans = limit * _MAX_SCAN_MULTIPLE
            scanned = 0
            while len(messages) < limit and cursor <= last_seq and scanned < max_scans:
                scanned += 1
                try:
                    # `seq` is the starting point in both modes, and `next` mode
                    # needs it as much as a plain read does: nats-py sends
                    # `{"seq": seq, "next_by_subj": subject}`, so passing None
                    # asks the server for the first match every time -- which
                    # returned the same message `limit` times over.
                    raw = await jsm.get_msg(
                        name,
                        seq=cursor,
                        subject=subject if use_next else None,
                        next=use_next,
                        direct=direct,
                    )
                except js_errors.NotFoundError:
                    if use_next:
                        break  # nothing further forward matches; stop rather than scan to the end
                    cursor += 1
                    continue
                messages.append(await self._stored_message(server_id, raw))
                cursor = (raw.seq or cursor) + 1
            return messages

    async def delete_message(self, server_id: uuid.UUID, name: str, seq: int) -> None:
        instance = f"/api/servers/{server_id}/jetstream/streams/{name}/messages/{seq}"
        async with jetstream(self._connections, server_id, instance) as js:
            await js.manager.delete_msg(name, seq)


def provide_jetstream(
    connections: NamedDependency[ConnectionManager], session: NamedDependency[AsyncSession]
) -> JetStreamService:
    return JetStreamService(connections, session)
