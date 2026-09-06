"""JetStream advisories and $SYS account events.

Two things this file refuses to do, both because the contract says so:

  - keep anything across a restart. `AdvisoryFeed` is an in-memory ring, and a
    fresh one starts at empty -- that is `AdvisoryFeedState.note`, enforced by
    never touching the database for anything but the capture stream.
  - invent server-side history. `seen` and every count here are `sampled`:
    true about the window this feed was listening, never a total the server
    itself would recognise.

Classification is deliberately tolerant. JetStream's own advisory shapes are not
part of nats-py, so this reads the raw JSON defensively -- `.get()` everywhere --
and falls back to `AdvisoryKind.OTHER` with the raw body shown rather than
raising on a field that turns out to be named differently on some server version.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections import deque
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final

from litestar.channels import ChannelsPlugin
from nats import errors as nats_errors
from nats.aio.msg import Msg
from nats.aio.subscription import Subscription as NatsSubscription
from nats.js import errors as js_errors
from nats.js.api import RetentionPolicy, StreamConfig

from nats_lens.conn.connection import ManagedConnection
from nats_lens.conn.errors import NatsProblem
from nats_lens.conn.manager import ConnectionManager
from nats_lens.domain.advisories.schemas import (
    AdvisoryAction,
    AdvisoryEvent,
    AdvisoryFeedState,
    AdvisoryKind,
    AdvisoryTypeCount,
    CaptureStreamRequest,
    Severity,
)
from nats_lens.domain.ws import Advisory

JS_ADVISORY_SUBJECT: Final = "$JS.EVENT.ADVISORY.>"
SYS_ACCOUNT_SUBJECT: Final = "$SYS.ACCOUNT.*.>"
DEFAULT_BUFFER_SIZE: Final = 500
"""How many events this process remembers per server. A ring, not a log."""

# ------------------------------------------------------------------ classification

_KIND_BY_NEEDLE: Final[tuple[tuple[str, AdvisoryKind], ...]] = (
    ("max_deliver", AdvisoryKind.MAX_DELIVERIES),
    ("delivery_exceeded", AdvisoryKind.MAX_DELIVERIES),
    ("nak", AdvisoryKind.NAK),
    ("terminated", AdvisoryKind.TERMINATED),
    ("msg_ack", AdvisoryKind.ACK),
    ("consumer_leader_elected", AdvisoryKind.LEADER_ELECTED),
    ("stream_leader_elected", AdvisoryKind.LEADER_ELECTED),
    ("quorum_lost", AdvisoryKind.QUORUM_LOST),
    ("consumer_create", AdvisoryKind.CONSUMER_ACTION),
    ("consumer_delete", AdvisoryKind.CONSUMER_ACTION),
    ("consumer_pause", AdvisoryKind.CONSUMER_ACTION),
    ("consumer_action", AdvisoryKind.CONSUMER_ACTION),
    ("stream_create", AdvisoryKind.STREAM_ACTION),
    ("stream_delete", AdvisoryKind.STREAM_ACTION),
    ("stream_update", AdvisoryKind.STREAM_ACTION),
    ("stream_action", AdvisoryKind.STREAM_ACTION),
    ("client_connect", AdvisoryKind.CLIENT_CONNECT),
    ("client_disconnect", AdvisoryKind.CLIENT_DISCONNECT),
    ("statsz", AdvisoryKind.SERVER_STATSZ),
)
"""Matched against `f"{type} {subject}".lower()`, in order. The `type` field --
`io.nats.jetstream.advisory.v1.max_deliver` and friends -- is the strong signal;
the subject is what is left when an event carries no recognisable `type` at all."""

SEVERITY_BY_KIND: Final[dict[AdvisoryKind, Severity]] = {
    AdvisoryKind.MAX_DELIVERIES: Severity.ALERT,
    AdvisoryKind.QUORUM_LOST: Severity.ALERT,
    AdvisoryKind.NAK: Severity.WARNING,
    AdvisoryKind.TERMINATED: Severity.WARNING,
    AdvisoryKind.LEADER_ELECTED: Severity.NOTICE,
    AdvisoryKind.CONSUMER_ACTION: Severity.NOTICE,
    AdvisoryKind.STREAM_ACTION: Severity.NOTICE,
    AdvisoryKind.CLIENT_CONNECT: Severity.INFO,
    AdvisoryKind.CLIENT_DISCONNECT: Severity.INFO,
    AdvisoryKind.SERVER_STATSZ: Severity.INFO,
    AdvisoryKind.ACK: Severity.INFO,
    AdvisoryKind.OTHER: Severity.INFO,
}

KIND_LABELS: Final[dict[AdvisoryKind, str]] = {
    AdvisoryKind.MAX_DELIVERIES: "Max deliveries",
    AdvisoryKind.NAK: "Nak",
    AdvisoryKind.TERMINATED: "Terminated",
    AdvisoryKind.ACK: "Ack",
    AdvisoryKind.CONSUMER_ACTION: "Consumer action",
    AdvisoryKind.STREAM_ACTION: "Stream action",
    AdvisoryKind.LEADER_ELECTED: "Leader elected",
    AdvisoryKind.QUORUM_LOST: "Quorum lost",
    AdvisoryKind.CLIENT_CONNECT: "Client connect",
    AdvisoryKind.CLIENT_DISCONNECT: "Client disconnect",
    AdvisoryKind.SERVER_STATSZ: "Server statsz",
    AdvisoryKind.OTHER: "Other",
}


def classify_kind(type_url: str, subject: str) -> AdvisoryKind:
    haystack = f"{type_url} {subject}".lower()
    for needle, kind in _KIND_BY_NEEDLE:
        if needle in haystack:
            return kind
    return AdvisoryKind.OTHER


def _parse_json(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError, TypeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _pretty(raw: bytes, parsed: dict[str, Any]) -> str:
    """What the server actually sent, pretty-printed. Never nats-lens's paraphrase."""
    if parsed:
        return json.dumps(parsed, indent=2, sort_keys=True)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return repr(raw)


def _advisory_time(parsed: dict[str, Any]) -> datetime:
    raw_ts = parsed.get("time") or parsed.get("timestamp")
    if isinstance(raw_ts, str):
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
    return datetime.now(UTC)


def _account_from_subject(subject: str) -> str | None:
    tokens = subject.split(".")
    if len(tokens) >= 3 and tokens[0] == "$SYS" and tokens[1] == "ACCOUNT":
        return tokens[2]
    return None


def _target(kind: AdvisoryKind, subject: str, parsed: dict[str, Any]) -> str:
    """`ORDERS - search-index`, assembled for the list row."""
    stream = parsed.get("stream")
    consumer = parsed.get("consumer")
    if stream and consumer:
        return f"{stream} · {consumer}"
    if stream:
        return str(stream)

    client = parsed.get("client")
    if isinstance(client, dict):
        account = client.get("acc") or _account_from_subject(subject)
        name = client.get("name") or client.get("user")
        if account and name:
            return f"{account} · {name}"
        if account:
            return str(account)

    account = _account_from_subject(subject)
    return account or subject


def _client_summary(parsed: dict[str, Any], verb: str) -> str:
    client = parsed.get("client")
    if not isinstance(client, dict):
        return f"a client {verb}"
    cid = client.get("id")
    bits = [f"cid {cid}" if cid is not None else "a client", verb]
    if client.get("host"):
        bits.append(f"from {client['host']}")
    if client.get("user"):
        bits.append(f"as {client['user']}")
    return " ".join(bits)


def _summary(kind: AdvisoryKind, parsed: dict[str, Any]) -> str:
    stream_seq = parsed.get("stream_seq")
    deliveries = parsed.get("deliveries")
    if kind is AdvisoryKind.MAX_DELIVERIES:
        return f"stream seq {stream_seq} gave up after {deliveries} attempts"
    if kind is AdvisoryKind.NAK:
        return f"handler naked seq {stream_seq}, attempt {deliveries}"
    if kind is AdvisoryKind.TERMINATED:
        reason = parsed.get("reason")
        return (
            f"seq {stream_seq} terminated: {reason}" if reason else f"seq {stream_seq} terminated"
        )
    if kind is AdvisoryKind.LEADER_ELECTED:
        leader, stream = parsed.get("leader"), parsed.get("stream")
        return (
            f"{leader} took leadership of {stream}" if leader and stream else "leadership changed"
        )
    if kind is AdvisoryKind.QUORUM_LOST:
        stream = parsed.get("stream")
        return f"{stream} lost quorum" if stream else "quorum lost"
    if kind is AdvisoryKind.CONSUMER_ACTION:
        return f"{parsed.get('consumer', 'a consumer')} {parsed.get('action', 'changed')}"
    if kind is AdvisoryKind.STREAM_ACTION:
        return f"{parsed.get('stream', 'a stream')} {parsed.get('action', 'changed')}"
    if kind is AdvisoryKind.CLIENT_CONNECT:
        return _client_summary(parsed, "connected")
    if kind is AdvisoryKind.CLIENT_DISCONNECT:
        return _client_summary(parsed, "disconnected")
    type_url = parsed.get("type")
    return str(type_url) if type_url else "advisory received"


def _actions(kind: AdvisoryKind, parsed: dict[str, Any]) -> tuple[AdvisoryAction, ...]:
    stream, consumer = parsed.get("stream"), parsed.get("consumer")
    actions: list[AdvisoryAction] = []
    if stream and consumer:
        actions.append(
            AdvisoryAction(
                label=f"Open {consumer} consumer",
                kind="open_consumer",
                target=f"{stream}/{consumer}",
            )
        )
    elif stream:
        actions.append(
            AdvisoryAction(label=f"Open {stream} stream", kind="open_stream", target=str(stream))
        )
    if kind is AdvisoryKind.MAX_DELIVERIES and stream and parsed.get("stream_seq") is not None:
        actions.append(
            AdvisoryAction(
                label=f"Republish seq {parsed['stream_seq']}",
                kind="republish",
                target=f"{stream}/{parsed['stream_seq']}",
            )
        )
    if kind in (AdvisoryKind.CLIENT_CONNECT, AdvisoryKind.CLIENT_DISCONNECT):
        client = parsed.get("client")
        cid = client.get("id") if isinstance(client, dict) else None
        if cid is not None:
            actions.append(
                AdvisoryAction(
                    label=f"Open connection {cid}", kind="open_connection", target=str(cid)
                )
            )
    return tuple(actions)


def _explanation(kind: AdvisoryKind, parsed: dict[str, Any]) -> str:
    """What it means and what usually causes it. Written by nats-lens, not the server."""
    if kind is AdvisoryKind.MAX_DELIVERIES:
        return (
            "This consumer reached max_deliver without an ack. The message stays in the "
            "stream, but this consumer will not be handed it again. Republishing it "
            "somewhere the consumer is not watching -- a dead-letter subject -- is the "
            "usual way to recover it."
        )
    if kind is AdvisoryKind.NAK:
        return (
            "An explicit negative ack. The server will redeliver after ack_wait, or after "
            "the delay the handler asked for. One nak on an early delivery is ordinary "
            "backpressure; the same message naking over and over is worth investigating."
        )
    if kind is AdvisoryKind.TERMINATED:
        return (
            "The handler called term() on this message, so the server stops delivering it "
            "to this consumer entirely. Unlike a nak, there is no further retry."
        )
    if kind is AdvisoryKind.LEADER_ELECTED:
        return (
            "Raft leadership moved to a new node. With only one replica there is nothing "
            "to fall back to, so a restart of the new leader makes this unavailable until "
            "it returns; more replicas make elections invisible to clients."
        )
    if kind is AdvisoryKind.QUORUM_LOST:
        return (
            "A clustered stream or consumer lost quorum: too few replicas are reachable to "
            "elect a leader, so it cannot accept new messages or acks until enough of them "
            "come back."
        )
    if kind is AdvisoryKind.CONSUMER_ACTION:
        return (
            "A consumer was created or deleted. Ephemeral consumers that are created but "
            "never cleaned up are the usual reason this is worth watching."
        )
    if kind is AdvisoryKind.STREAM_ACTION:
        return "A stream's configuration was created, updated or deleted."
    if kind is AdvisoryKind.CLIENT_CONNECT:
        return (
            "A client authenticated on this account. Connect and disconnect events only "
            "arrive while nats-lens holds a system account connection."
        )
    if kind is AdvisoryKind.CLIENT_DISCONNECT:
        reason = parsed.get("reason")
        base = "A client's connection closed."
        return f"{base} Reported reason: {reason}." if reason else base
    if kind is AdvisoryKind.SERVER_STATSZ:
        return "A periodic heartbeat of server-wide statistics, pushed rather than polled."
    if kind is AdvisoryKind.ACK:
        return (
            "A sampled acknowledgement, published because the consumer's sample_freq selected it."
        )
    type_url = parsed.get("type")
    if type_url:
        return (
            f"nats-lens does not recognise the advisory type {type_url!r} yet. "
            "The raw event is below."
        )
    return "An event arrived on the advisory subject that nats-lens could not parse as JSON."


def build_event(subject: str, raw: bytes) -> AdvisoryEvent:
    parsed = _parse_json(raw)
    type_url = str(parsed.get("type") or "")
    kind = classify_kind(type_url, subject)
    return AdvisoryEvent(
        id=str(parsed.get("id") or uuid.uuid4()),
        at=_advisory_time(parsed),
        kind=kind,
        severity=SEVERITY_BY_KIND.get(kind, Severity.INFO),
        type_url=type_url or "(no type field)",
        subject=subject,
        target=_target(kind, subject, parsed),
        summary=_summary(kind, parsed),
        body=_pretty(raw, parsed),
        explanation=_explanation(kind, parsed),
        actions=_actions(kind, parsed),
    )


# ------------------------------------------------------------------------ the feed


class AdvisoryFeed:
    """One server's advisory subscription, and the transient events it has kept.

    Never stored: a fresh `AdvisoryFeed` is what every reconnect gets, which is
    what makes `AdvisoryFeedState.note` true rather than aspirational. `capture`
    is the one thing that outlives it, and only because it lives in JetStream,
    not in this object.
    """

    def __init__(self, server_id: uuid.UUID, buffer_size: int = DEFAULT_BUFFER_SIZE) -> None:
        self.server_id = server_id
        self.channel = f"advisories:{server_id}"
        self._buffer_size = buffer_size
        self._events: deque[AdvisoryEvent] = deque(maxlen=buffer_size)
        self._counts: dict[AdvisoryKind, int] = {}
        self._listening = False
        self._started_at: datetime | None = None
        self._capture_stream: str | None = None
        self._js_sub: NatsSubscription | None = None
        self._sys_sub: NatsSubscription | None = None
        self._channels: ChannelsPlugin | None = None
        self._lock = asyncio.Lock()

    async def ensure_started(self, conn: ManagedConnection, channels: ChannelsPlugin) -> None:
        self._channels = channels
        if self._listening or conn.nc is None:
            return
        async with self._lock:
            if self._listening:
                return
            self._js_sub = await conn.nc.subscribe(JS_ADVISORY_SUBJECT, cb=self._on_event)
            if conn.system_connected and conn.sys_nc is not None:
                self._sys_sub = await conn.sys_nc.subscribe(SYS_ACCOUNT_SUBJECT, cb=self._on_event)
            self._listening = True
            self._started_at = datetime.now(UTC)

    async def _on_event(self, msg: Msg) -> None:
        self._record(build_event(msg.subject, msg.data))

    def _record(self, event: AdvisoryEvent) -> None:
        self._events.append(event)
        self._counts[event.kind] = self._counts.get(event.kind, 0) + 1
        if self._channels is not None:
            self._channels.publish(Advisory(channel=self.channel, event=event), self.channel)

    def list_events(self, kind: AdvisoryKind | None, limit: int) -> list[AdvisoryEvent]:
        events = list(self._events)
        events.reverse()  # newest first
        if kind is not None:
            events = [e for e in events if e.kind is kind]
        return events[: max(limit, 0)]

    def state(self) -> AdvisoryFeedState:
        return AdvisoryFeedState(
            listening=self._listening,
            started_at=self._started_at,
            seen=sum(self._counts.values()),
            buffer_size=self._buffer_size,
            capture_stream=self._capture_stream,
        )

    def counts(self) -> list[AdvisoryTypeCount]:
        return [
            AdvisoryTypeCount(
                kind=kind,
                label=KIND_LABELS.get(kind, kind.value),
                count=count,
                severity=SEVERITY_BY_KIND.get(kind, Severity.INFO),
            )
            for kind, count in sorted(self._counts.items(), key=lambda kv: (-kv[1], kv[0].value))
        ]

    async def create_capture(self, conn: ManagedConnection, request: CaptureStreamRequest) -> None:
        jsm = conn.jsm
        if jsm is None:
            raise nats_errors.ConnectionClosedError
        config = StreamConfig(
            name=request.name,
            subjects=list(request.subjects),
            max_age=request.max_age_seconds,
            max_msgs=request.max_msgs,
            num_replicas=request.replicas,
            retention=RetentionPolicy.LIMITS,
        )
        try:
            await jsm.stream_info(request.name)
        except js_errors.NotFoundError:
            await jsm.add_stream(config)
        else:
            await jsm.update_stream(config)
        self._capture_stream = request.name

    async def aclose(self) -> None:
        for sub in (self._js_sub, self._sys_sub):
            if sub is not None:
                with contextlib.suppress(Exception):
                    await sub.unsubscribe()
        self._js_sub = self._sys_sub = None
        self._listening = False
        self._started_at = None


class AdvisoryFeeds:
    """One feed per server, process-wide -- see `conn.multiplex` for why."""

    def __init__(self) -> None:
        self._feeds: dict[uuid.UUID, AdvisoryFeed] = {}

    def ensure(self, server_id: uuid.UUID) -> AdvisoryFeed:
        feed = self._feeds.get(server_id)
        if feed is None:
            feed = AdvisoryFeed(server_id)
            self._feeds[server_id] = feed
        return feed

    def get(self, server_id: uuid.UUID) -> AdvisoryFeed | None:
        return self._feeds.get(server_id)

    async def aclose(self) -> None:
        feeds, self._feeds = self._feeds, {}
        for feed in feeds.values():
            await feed.aclose()


_FEEDS: Final = AdvisoryFeeds()


def feeds() -> AdvisoryFeeds:
    return _FEEDS


# --------------------------------------------------------------------- the service


class AdvisoriesService:
    def __init__(self, connections: ConnectionManager, channels: ChannelsPlugin) -> None:
        self._connections = connections
        self._channels = channels

    async def list_events(
        self, server_id: uuid.UUID, kind: AdvisoryKind | None, limit: int
    ) -> list[AdvisoryEvent]:
        feed = await self._feed(server_id)
        return feed.list_events(kind, limit)

    async def state(self, server_id: uuid.UUID) -> AdvisoryFeedState:
        feed = await self._feed(server_id)
        return feed.state()

    async def counts(self, server_id: uuid.UUID) -> list[AdvisoryTypeCount]:
        feed = await self._feed(server_id)
        return feed.counts()

    async def create_capture(
        self, server_id: uuid.UUID, data: CaptureStreamRequest
    ) -> AdvisoryFeedState:
        conn = await self._connect(server_id)
        feed = await self._feed(server_id)
        try:
            await feed.create_capture(conn, data)
        except Exception as exc:
            raise NatsProblem.of(exc) from exc
        return feed.state()

    async def _feed(self, server_id: uuid.UUID) -> AdvisoryFeed:
        conn = await self._connect(server_id)
        feed = feeds().ensure(server_id)
        await feed.ensure_started(conn, self._channels)
        return feed

    async def _connect(self, server_id: uuid.UUID) -> ManagedConnection:
        try:
            return await self._connections.ensure(server_id)
        except Exception as exc:
            raise NatsProblem.of(exc) from exc


def _headers_or_empty(headers: Mapping[str, str] | None) -> dict[str, str]:
    return dict(headers) if headers else {}
