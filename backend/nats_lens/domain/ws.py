"""The websocket frame contract.

FROZEN CONTRACT -- see domain/common.py.

Read-path and control only. Subscriptions are created over HTTP
(`POST /api/servers/{id}/core/subscriptions`), which returns a channel name; the
socket then asks to be joined to that channel. Publishing, requesting and every
other mutation is HTTP too. The socket therefore carries no side effects, which
is what makes it safe to reconnect blindly and easy to test.

Fan-out and backpressure are Litestar's `ChannelsPlugin`
(`subscriber_backlog_strategy="dropleft"`), not hand-rolled.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import msgspec

from nats_lens.domain.advisories.schemas import AdvisoryEvent
from nats_lens.domain.core.schemas import TranscriptRow
from nats_lens.domain.monitor.schemas import RateSample, VarzSummary
from nats_lens.domain.servers.schemas import ConnectionState

# ---------------------------------------------------------------- client -> server


class Join(msgspec.Struct, tag="join", tag_field="op", frozen=True):
    """Start receiving a channel the client already created over HTTP."""

    channel: str


class Leave(msgspec.Struct, tag="leave", tag_field="op", frozen=True):
    channel: str


class Ping(msgspec.Struct, tag="ping", tag_field="op", frozen=True):
    at: datetime | None = None


ClientFrame = Annotated[Join | Leave | Ping, msgspec.Meta(title="ClientFrame")]

# ---------------------------------------------------------------- server -> client


class Joined(msgspec.Struct, tag="joined", tag_field="t", frozen=True):
    channel: str


class Left(msgspec.Struct, tag="left", tag_field="t", frozen=True):
    channel: str


class Message(msgspec.Struct, tag="msg", tag_field="t", frozen=True):
    """A transcript row. Payload is truncated -- fetch the full one by capture_id."""

    channel: str
    row: TranscriptRow


class Dropped(msgspec.Struct, tag="dropped", tag_field="t", frozen=True):
    """nats-lens fell behind and discarded messages. Shown as a transcript row.

    The same honesty rule as the source badges, turned on our own limits.
    """

    channel: str
    count: int
    since: datetime


class ServerStatus(msgspec.Struct, tag="status", tag_field="t", frozen=True):
    """Pushed from nats-py's connection callbacks, so the sidebar dot is not polled."""

    server_id: str
    state: ConnectionState
    detail: str | None = None
    rtt_ms: float | None = None


class Advisory(msgspec.Struct, tag="advisory", tag_field="t", frozen=True):
    channel: str
    event: AdvisoryEvent


class MonitorSample(msgspec.Struct, tag="monitor", tag_field="t", frozen=True):
    """One poll of /varz, with the rate delta against the previous poll."""

    channel: str
    varz: VarzSummary
    rates: RateSample | None = None


class KvChange(msgspec.Struct, tag="kv", tag_field="t", frozen=True):
    channel: str
    bucket: str
    key: str
    revision: int
    operation: str


class WsError(msgspec.Struct, tag="error", tag_field="t", frozen=True):
    detail: str
    channel: str | None = None


class Pong(msgspec.Struct, tag="pong", tag_field="t", frozen=True):
    at: datetime


ServerFrame = Annotated[
    Joined
    | Left
    | Message
    | Dropped
    | ServerStatus
    | Advisory
    | MonitorSample
    | KvChange
    | WsError
    | Pong,
    msgspec.Meta(title="ServerFrame"),
]
