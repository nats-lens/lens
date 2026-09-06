"""Core NATS: subscribe, publish, request, and the message inspector.

FROZEN CONTRACT -- see domain/common.py.

Note the split: the websocket is read-path and control only. Every mutation
(publish, request) is an HTTP call. That keeps the socket idempotent and lets the
transcript tests drive it without racing.
"""


# instance rather than sharing them, so `= {}` here is safe. Verified.

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import msgspec

from nats_lens.codec.schemas import Decoded


class Direction(StrEnum):
    IN = "IN"
    OUT = "OUT"


class TranscriptRow(msgspec.Struct, frozen=True):
    """A row in the live transcript.

    Deliberately small and payload-truncated: the firehose must not carry hex
    dumps. The full message is fetched by `capture_id` when a row is selected.
    """

    capture_id: str
    seq: int
    at: datetime
    direction: Direction
    subject: str
    reply: str | None
    size: int
    headers_count: int
    codec: str
    preview: str
    """One line, already decoded, at most 120 characters."""
    truncated: bool = False


class CapturedMessage(msgspec.Struct, frozen=True):
    """The full message behind a transcript row, with every inspector view."""

    capture_id: str
    seq: int
    at: datetime
    direction: Direction
    subject: str
    reply: str | None
    size: int
    headers: dict[str, str]
    payload_b64: str
    decoded: Decoded


class SubscriptionCreate(msgspec.Struct, frozen=True):
    subject: str
    queue: str | None = None
    rate_cap: int = 200
    """Messages per second before nats-lens starts dropping and saying so."""


class SubscriptionInfo(msgspec.Struct, frozen=True):
    id: uuid.UUID
    subject: str
    queue: str | None
    channel: str
    """The channel name the websocket subscribes to."""
    delivered: int
    dropped: int
    """Surfaced in the transcript as a row. Our own limits get the same honesty."""
    started_at: datetime


class PublishRequest(msgspec.Struct, frozen=True):
    subject: str
    payload_b64: str
    headers: dict[str, str] = {}
    reply: str | None = None


class PublishResult(msgspec.Struct, frozen=True):
    ok: bool
    subject: str
    size: int


class RequestRequest(msgspec.Struct, frozen=True):
    subject: str
    payload_b64: str
    headers: dict[str, str] = {}
    timeout_seconds: float = 2.0


class RequestResult(msgspec.Struct, frozen=True):
    ok: bool
    elapsed_ms: float
    reply: CapturedMessage | None = None
    error: str | None = None
    """`nats.errors.NoRespondersError` and friends, verbatim."""


class SubjectChip(msgspec.Struct, frozen=True):
    """A saved filter with what nats-lens has actually seen on it. `sampled`."""

    id: uuid.UUID | None
    label: str
    subject: str
    seen: int
