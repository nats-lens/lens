"""The subscription hub for the Core screen. OWNER: agent B5-messaging.

One real `nc.subscribe()` per `(server_id, subject, queue)`, refcounted: ten
browser tabs watching `orders.>` cost one NATS subscription, and the last
unsubscriber drops it. Everything else -- the token-bucket rate cap, the capture
ring, the drop counter -- hangs off that one shared subscription so every lease
on it sees the same transcript.

Fan-out to browsers is `ChannelsPlugin`'s job, not this module's: a message that
passes the rate cap is decoded once and published to the subscription's channel,
and however many websockets have joined that channel each get their own copy
with their own backpressure. This module never touches a websocket.

Process-wide by construction, like `SchemaRegistry` and `MonitorPollers` --
nats-lens refuses to run with more than one worker precisely because state like
this lives in memory and a second worker would silently split it.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import functools
import hashlib
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Final

from litestar.channels import ChannelsPlugin
from nats import errors as nats_errors
from nats.aio.msg import Msg
from nats.aio.subscription import Subscription as NatsSubscription

from nats_lens.codec.rules import pattern_matches
from nats_lens.codec.schemas import Codec, Decoded
from nats_lens.conn.connection import ManagedConnection
from nats_lens.domain.core.schemas import (
    CapturedMessage,
    Direction,
    SubscriptionCreate,
    SubscriptionInfo,
    TranscriptRow,
)
from nats_lens.domain.ws import Dropped
from nats_lens.domain.ws import Message as WsMessage

DecodeHook = Callable[[bytes, str, Mapping[str, str]], Awaitable[Decoded]]
"""Runs the codec chain and feeds `Samples`. Supplied by `CoreService`, which is
the one place that knows how to reach the schema registry -- this module stays
free of the database and of `domain.protoschemas`."""

CAPTURE_RING_SIZE: Final = 2000
"""How many full messages one subscription remembers for `GET /core/messages/{id}`."""

PENDING_OUT_SIZE: Final = 256
"""How many of our own recent publishes we watch for in the echo. Bounded so a
publisher that never gets echoed (no_echo, or nobody subscribed) cannot leak."""

DROP_EMIT_INTERVAL: Final = 1.0
"""Dropped frames are coalesced to at most one per subscription per second --
emitting one per drop would just add more traffic to a channel that is already
falling behind."""

PREVIEW_MAX_CHARS: Final = 120

DEFAULT_RATE_CAP: Final = 200


class _TokenBucket:
    """Messages per second, with a one-second burst allowance."""

    __slots__ = ("capacity", "rate", "tokens", "updated")

    def __init__(self, rate: float) -> None:
        self.rate = max(rate, 0.0)
        self.capacity = max(self.rate, 1.0)
        self.tokens = self.capacity
        self.updated = time.monotonic()

    def set_rate(self, rate: float) -> None:
        self.rate = max(rate, 0.0)
        self.capacity = max(self.rate, 1.0)
        self.tokens = min(self.tokens, self.capacity)

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class _Subscription:
    """One real `nc.subscribe()`, shared by every lease that asked for the same subject.

    A lease is one `POST /core/subscriptions` call. `leases` maps each lease id to
    the rate cap it asked for; the bucket runs at the highest of them, so joining
    an already-open subscription can only ever raise its ceiling, never lower it
    out from under someone else who is still watching.
    """

    __slots__ = (
        "_capture_order",
        "_captures",
        "_dropped_since_emit",
        "_last_drop_emit",
        "_pending_out",
        "_pending_out_set",
        "bucket",
        "channel",
        "delivered",
        "dropped",
        "leases",
        "nats_sub",
        "queue",
        "server_id",
        "started_at",
        "subject",
        "uid",
    )

    def __init__(self, server_id: uuid.UUID, subject: str, queue: str | None) -> None:
        self.uid = uuid.uuid4()
        self.server_id = server_id
        self.subject = subject
        self.queue = queue
        self.channel = f"core:{server_id}:{self.uid.hex}"
        self.started_at = datetime.now(UTC)
        self.delivered = 0
        self.dropped = 0
        self.leases: dict[uuid.UUID, int] = {}
        self.bucket = _TokenBucket(DEFAULT_RATE_CAP)
        self.nats_sub: NatsSubscription | None = None
        self._capture_order: deque[str] = deque(maxlen=CAPTURE_RING_SIZE)
        self._captures: dict[str, CapturedMessage] = {}
        self._pending_out: deque[bytes] = deque(maxlen=PENDING_OUT_SIZE)
        self._pending_out_set: set[bytes] = set()
        self._dropped_since_emit = 0
        self._last_drop_emit = 0.0

    def key(self) -> tuple[uuid.UUID, str, str | None]:
        return (self.server_id, self.subject, self.queue)

    def rate_cap(self) -> int:
        return max(self.leases.values()) if self.leases else DEFAULT_RATE_CAP

    # ---------------------------------------------------------------- capture ring

    def remember(self, message: CapturedMessage) -> None:
        """Bounded FIFO, keyed by `capture_id`. The oldest is dropped, never stored."""
        if len(self._capture_order) == self._capture_order.maxlen:
            oldest = self._capture_order.popleft()
            self._captures.pop(oldest, None)
        self._capture_order.append(message.capture_id)
        self._captures[message.capture_id] = message

    def capture(self, capture_id: str) -> CapturedMessage | None:
        return self._captures.get(capture_id)

    # ------------------------------------------------------------ self-echo dedupe

    def mark_pending_out(self, fingerprint: bytes) -> None:
        if len(self._pending_out) == self._pending_out.maxlen:
            oldest = self._pending_out.popleft()
            self._pending_out_set.discard(oldest)
        self._pending_out.append(fingerprint)
        self._pending_out_set.add(fingerprint)

    def is_own_echo(self, subject: str, payload: bytes, headers: Mapping[str, str]) -> bool:
        """Whether this message is one we published, and if so, forget it.

        The fingerprint is computed lazily. Nothing is pending unless someone has
        published from the UI in the last `PENDING_OUT_SIZE` messages, which on a
        firehose being watched is almost never -- and hashing every payload to
        answer "no" is the one avoidable cost on the delivery path.
        """
        if not self._pending_out_set:
            return False
        return self._consume_pending_out(_fingerprint(subject, payload, headers))

    def _consume_pending_out(self, fingerprint: bytes) -> bool:
        if fingerprint not in self._pending_out_set:
            return False
        self._pending_out_set.discard(fingerprint)
        with contextlib.suppress(ValueError):
            self._pending_out.remove(fingerprint)
        return True

    # ------------------------------------------------------------------- dropping

    def note_drop(self) -> tuple[int, datetime] | None:
        """Count a drop always; return `(count, since)` only when it is time to say so.

        `dropped` (surfaced on `SubscriptionInfo`) is exact on every call. The
        `Dropped` frame itself is coalesced to `DROP_EMIT_INTERVAL` so a firehose
        that is already overwhelming the rate cap does not also flood the channel
        that is supposed to be relieving it.
        """
        self.dropped += 1
        self._dropped_since_emit += 1
        now = time.monotonic()
        if now - self._last_drop_emit < DROP_EMIT_INTERVAL:
            return None
        self._last_drop_emit = now
        count, self._dropped_since_emit = self._dropped_since_emit, 0
        return count, datetime.now(UTC)


def _fingerprint(subject: str, payload: bytes, headers: Mapping[str, str] | None) -> bytes:
    """Enough of a message's identity to recognise our own echo, cheaply."""
    digest = hashlib.blake2b(digest_size=16)
    digest.update(subject.encode())
    digest.update(b"\0")
    digest.update(payload)
    if headers:
        digest.update(b"\0")
        for k in sorted(headers):
            digest.update(k.encode())
            digest.update(b"=")
            digest.update(headers[k].encode())
            digest.update(b"&")
    return digest.digest()


def _clip(text: str) -> tuple[str, bool]:
    if len(text) <= PREVIEW_MAX_CHARS:
        return text, False
    return text[: PREVIEW_MAX_CHARS - 1] + "…", True


def _preview(decoded: Decoded, size: int) -> tuple[str, bool]:
    """One line, already decoded, at most 120 characters. Never a hex dump.

    The full payload -- protobuf fields, wire fields, hex, everything the
    inspector shows -- lives on `CapturedMessage` and is fetched by `capture_id`.
    The firehose only ever carries this line.
    """
    if decoded.codec is Codec.EMPTY:
        return "(empty)", False
    if decoded.text is not None:
        # Whitespace is collapsed rather than the first line taken: decoded JSON
        # is pretty-printed, so its first line is `{` and every row in the
        # transcript would look identical.
        return _clip(" ".join(decoded.text.split()))
    if decoded.fields:
        type_name = decoded.type_name or "message"
        body = "  ".join(f"{f.name}={f.value}" for f in decoded.fields)
        return _clip(f"{type_name}  {body}" if body else type_name)
    if decoded.wire_fields:
        head = "  ".join(
            f"{f.wire_type.value}({f.field_number})={f.render}" for f in decoded.wire_fields[:3]
        )
        return _clip(
            f"{decoded.codec.value} · {len(decoded.wire_fields)} fields · no schema · {head}"
        )
    return _clip(f"{decoded.codec.value} · {size} bytes")


def _build_captured(
    capture_id: str,
    seq: int,
    direction: Direction,
    subject: str,
    reply: str | None,
    headers: Mapping[str, str],
    payload: bytes,
    decoded: Decoded,
) -> CapturedMessage:
    return CapturedMessage(
        capture_id=capture_id,
        seq=seq,
        at=datetime.now(UTC),
        direction=direction,
        subject=subject,
        reply=reply or None,
        size=len(payload),
        headers=dict(headers),
        payload_b64=base64.b64encode(payload).decode(),
        decoded=decoded,
    )


def _row_from_captured(captured: CapturedMessage) -> TranscriptRow:
    preview, clipped = _preview(captured.decoded, captured.size)
    return TranscriptRow(
        capture_id=captured.capture_id,
        seq=captured.seq,
        at=captured.at,
        direction=captured.direction,
        subject=captured.subject,
        reply=captured.reply,
        size=captured.size,
        headers_count=len(captured.headers),
        codec=captured.decoded.codec.value,
        preview=preview,
        truncated=clipped or captured.decoded.truncated,
    )


def _info(sub: _Subscription, lease_id: uuid.UUID) -> SubscriptionInfo:
    return SubscriptionInfo(
        id=lease_id,
        subject=sub.subject,
        queue=sub.queue,
        channel=sub.channel,
        delivered=sub.delivered,
        dropped=sub.dropped,
        started_at=sub.started_at,
    )


class Multiplexer:
    """Every live subscription, refcounted, across every server."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[uuid.UUID, str, str | None], _Subscription] = {}
        self._by_uid: dict[str, _Subscription] = {}
        self._by_lease: dict[uuid.UUID, _Subscription] = {}
        self._lock = asyncio.Lock()

    # -------------------------------------------------------------- leases

    async def subscribe(
        self,
        conn: ManagedConnection,
        server_id: uuid.UUID,
        create: SubscriptionCreate,
        channels: ChannelsPlugin,
        decode: DecodeHook,
    ) -> SubscriptionInfo:
        """Open the real subscription on the first lease; every later one just joins.

        The rate cap the caller asked for only ever raises the shared bucket -- see
        `_Subscription.rate_cap`.
        """
        queue = create.queue or None
        key = (server_id, create.subject, queue)
        lease_id = uuid.uuid4()
        async with self._lock:
            sub = self._by_key.get(key)
            if sub is None:
                if conn.nc is None:
                    raise nats_errors.ConnectionClosedError
                new_sub = _Subscription(server_id, create.subject, queue)
                new_sub.nats_sub = await conn.nc.subscribe(
                    create.subject,
                    queue=queue or "",
                    cb=functools.partial(self._on_message, new_sub, channels, decode),
                )
                self._by_key[key] = new_sub
                self._by_uid[new_sub.uid.hex] = new_sub
                sub = new_sub
            sub.leases[lease_id] = max(create.rate_cap, 0)
            sub.bucket.set_rate(sub.rate_cap())
            self._by_lease[lease_id] = sub
        return _info(sub, lease_id)

    async def unsubscribe(self, server_id: uuid.UUID, lease_id: uuid.UUID) -> bool:
        """Drop one lease. The real subscription goes only when the last one does."""
        async with self._lock:
            sub = self._by_lease.get(lease_id)
            if sub is None or sub.server_id != server_id:
                return False
            del self._by_lease[lease_id]
            sub.leases.pop(lease_id, None)
            if sub.leases:
                sub.bucket.set_rate(sub.rate_cap())
                return True
            self._by_key.pop(sub.key(), None)
            self._by_uid.pop(sub.uid.hex, None)
        if sub.nats_sub is not None:
            with contextlib.suppress(Exception):
                await sub.nats_sub.unsubscribe()
        return True

    def list_subscriptions(self, server_id: uuid.UUID) -> list[SubscriptionInfo]:
        return [
            _info(sub, lease_id)
            for lease_id, sub in self._by_lease.items()
            if sub.server_id == server_id
        ]

    def get_capture(self, server_id: uuid.UUID, capture_id: str) -> CapturedMessage | None:
        uid, _, _seq = capture_id.partition(":")
        sub = self._by_uid.get(uid)
        if sub is None or sub.server_id != server_id:
            return None
        return sub.capture(capture_id)

    def seen(self, server_id: uuid.UUID, subject: str) -> int:
        """`sampled`: the delivered count of a live subscription on this exact subject.

        A saved filter nats-lens has not actively watched this session reads zero,
        which is the honest answer -- it is not a server-side total and was never
        going to be one.
        """
        sub = self._by_key.get((server_id, subject, None))
        return sub.delivered if sub is not None else 0

    # -------------------------------------------------------------- the firehose

    async def _on_message(
        self, sub: _Subscription, channels: ChannelsPlugin, decode: DecodeHook, msg: Msg
    ) -> None:
        headers = msg.headers or {}
        if sub.is_own_echo(msg.subject, msg.data, headers):
            # Our own publish, echoed back by the server. `note_publish` already
            # emitted the OUT row for it.
            return
        if not sub.bucket.allow():
            dropped = sub.note_drop()
            if dropped is not None:
                count, since = dropped
                channels.publish(
                    Dropped(channel=sub.channel, count=count, since=since), sub.channel
                )
            return
        decoded = await decode(msg.data, msg.subject, headers)
        await self._emit(
            sub, channels, Direction.IN, msg.subject, msg.reply or None, headers, msg.data, decoded
        )

    async def note_publish(
        self,
        server_id: uuid.UUID,
        subject: str,
        payload: bytes,
        headers: Mapping[str, str],
        channels: ChannelsPlugin,
        decode: DecodeHook,
    ) -> None:
        """Show an HTTP publish or request in every live transcript it lands in.

        NATS echoes a publisher's own message back to its own matching
        subscriptions by default, so without the dedupe in `_on_message` this would
        show up twice: once here as an OUT row, and again moments later as an
        indistinguishable IN one.
        """
        matches = [
            sub
            for sub in self._by_key.values()
            if sub.server_id == server_id and pattern_matches(sub.subject, subject)
        ]
        if not matches:
            return
        decoded = await decode(payload, subject, headers)
        fingerprint = _fingerprint(subject, payload, headers)
        for sub in matches:
            sub.mark_pending_out(fingerprint)
            await self._emit(sub, channels, Direction.OUT, subject, None, headers, payload, decoded)

    async def _emit(
        self,
        sub: _Subscription,
        channels: ChannelsPlugin,
        direction: Direction,
        subject: str,
        reply: str | None,
        headers: Mapping[str, str],
        payload: bytes,
        decoded: Decoded,
    ) -> None:
        sub.delivered += 1
        seq = sub.delivered
        captured = _build_captured(
            f"{sub.uid.hex}:{seq}", seq, direction, subject, reply, headers, payload, decoded
        )
        sub.remember(captured)
        channels.publish(
            WsMessage(channel=sub.channel, row=_row_from_captured(captured)), sub.channel
        )

    # -------------------------------------------------------------- lifecycle

    async def aclose(self) -> None:
        """Drop every subscription. Tests use this between runs; nothing else does --
        the multiplexer otherwise lives for the process."""
        subs = list(self._by_key.values())
        self._by_key.clear()
        self._by_uid.clear()
        self._by_lease.clear()
        for sub in subs:
            if sub.nats_sub is not None:
                with contextlib.suppress(Exception):
                    await sub.nats_sub.unsubscribe()


_MULTIPLEXER: Final = Multiplexer()


def multiplexer() -> Multiplexer:
    return _MULTIPLEXER
