"""Core NATS: subscribe, publish, request, and the message inspector.

Judgement lives here, thin in the controller: connecting, decoding and feeding
the multiplexer are each one call from the caller's point of view.

`_decode_hook` is the one bridge between this domain and `domain.protoschemas`.
It opens its own short-lived session per call rather than reusing the request's
`session`, because the multiplexer's background NATS callbacks outlive any single
HTTP request -- the hook has to keep working long after the request that created
the subscription has finished. `SchemaRegistry` caches the compiled descriptors
and rules in the process, so on a warm cache this never touches the database at
all; it only does real I/O the first time, or after a schema edit invalidates it.
"""

from __future__ import annotations

import base64
import binascii
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

from litestar.channels import ChannelsPlugin
from litestar.exceptions import NotFoundException, ValidationException
from nats import errors as nats_errors
from nats.aio.client import Client
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nats_lens.codec import chain
from nats_lens.codec.schemas import Decoded
from nats_lens.conn.connection import ManagedConnection
from nats_lens.conn.errors import NatsProblem, describe
from nats_lens.conn.manager import ConnectionManager
from nats_lens.conn.multiplex import DecodeHook, multiplexer
from nats_lens.db.models import SavedFilter
from nats_lens.domain.core.schemas import (
    CapturedMessage,
    Direction,
    PublishRequest,
    PublishResult,
    RequestRequest,
    RequestResult,
    SubjectChip,
    SubscriptionCreate,
    SubscriptionInfo,
)
from nats_lens.domain.protoschemas.repository import SchemaRepository
from nats_lens.domain.protoschemas.service import REGISTRY, SAMPLES

FLUSH_TIMEOUT_SECONDS = 2
"""Seconds. nats-py types `flush(timeout=...)` as an int, so this is one."""
"""Publish waits for the write to actually leave the socket. A click that looks
like it worked but has not left the process yet is worse than a slower click."""


class CoreService:
    def __init__(
        self,
        session: AsyncSession,
        connections: ConnectionManager,
        channels: ChannelsPlugin,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session = session
        self._connections = connections
        self._channels = channels
        self._session_factory = session_factory

    # ------------------------------------------------------------ subscriptions

    async def list_subscriptions(self, server_id: uuid.UUID) -> list[SubscriptionInfo]:
        return multiplexer().list_subscriptions(server_id)

    async def subscribe(self, server_id: uuid.UUID, data: SubscriptionCreate) -> SubscriptionInfo:
        conn = await self._connect(server_id)
        try:
            return await multiplexer().subscribe(
                conn, server_id, data, self._channels, self._decode_hook(server_id)
            )
        except Exception as exc:
            raise NatsProblem.of(exc) from exc

    async def unsubscribe(self, server_id: uuid.UUID, sub_id: uuid.UUID) -> None:
        if not await multiplexer().unsubscribe(server_id, sub_id):
            raise NotFoundException(detail=f"No subscription {sub_id} is active on this server.")

    # ------------------------------------------------------------------- publish

    async def publish(self, server_id: uuid.UUID, data: PublishRequest) -> PublishResult:
        nc = await self._client(server_id)
        payload = _b64decode(data.payload_b64, "payload_b64")
        try:
            await nc.publish(
                data.subject, payload, reply=data.reply or "", headers=data.headers or None
            )
            await nc.flush(timeout=FLUSH_TIMEOUT_SECONDS)
        except Exception as exc:
            raise NatsProblem.of(exc) from exc
        await multiplexer().note_publish(
            server_id,
            data.subject,
            payload,
            data.headers,
            self._channels,
            self._decode_hook(server_id),
        )
        return PublishResult(ok=True, subject=data.subject, size=len(payload))

    # ------------------------------------------------------------------- request

    async def request(self, server_id: uuid.UUID, data: RequestRequest) -> RequestResult:
        """Request-reply, with `NoRespondersError` (and a timeout) as a *result*.

        The design shows a request that nobody answered as an outcome on screen,
        not a stack trace, so those two are caught here and turned into
        `RequestResult(ok=False, error=...)` rather than an HTTP error. Anything
        else -- the connection itself being down -- is still a real failure and
        goes through `NatsProblem` like every other endpoint.
        """
        nc = await self._client(server_id)
        payload = _b64decode(data.payload_b64, "payload_b64")
        started = time.monotonic()
        try:
            reply = await nc.request(
                data.subject, payload, timeout=data.timeout_seconds, headers=data.headers or None
            )
        except (nats_errors.NoRespondersError, nats_errors.TimeoutError) as exc:
            outcome = RequestResult(ok=False, elapsed_ms=_elapsed_ms(started), error=describe(exc))
        except Exception as exc:
            raise NatsProblem.of(exc) from exc
        else:
            reply_headers = dict(reply.headers or {})
            decoded = await self._decode_hook(server_id)(reply.data, reply.subject, reply_headers)
            captured = CapturedMessage(
                capture_id=uuid.uuid4().hex,
                seq=0,
                at=datetime.now(UTC),
                direction=Direction.IN,
                subject=reply.subject,
                reply=reply.reply or None,
                size=len(reply.data),
                headers=reply_headers,
                payload_b64=base64.b64encode(reply.data).decode(),
                decoded=decoded,
            )
            outcome = RequestResult(ok=True, elapsed_ms=_elapsed_ms(started), reply=captured)

        # The request itself went out over the wire whether or not anything
        # answered, so it belongs in any live transcript watching that subject
        # either way.
        await multiplexer().note_publish(
            server_id,
            data.subject,
            payload,
            data.headers,
            self._channels,
            self._decode_hook(server_id),
        )
        return outcome

    # -------------------------------------------------------------- the inspector

    async def get_message(self, server_id: uuid.UUID, capture_id: str) -> CapturedMessage:
        captured = multiplexer().get_capture(server_id, capture_id)
        if captured is None:
            raise NotFoundException(
                detail=f"No captured message {capture_id!r} on this server. It may have "
                "scrolled out of the capture ring, or the subscription that held it is gone."
            )
        return captured

    # ------------------------------------------------------------------- chips

    async def chips(self, server_id: uuid.UUID) -> list[SubjectChip]:
        rows = (
            (
                await self._session.execute(
                    select(SavedFilter)
                    .where(SavedFilter.server_id == server_id, SavedFilter.kind == "core")
                    .order_by(SavedFilter.sort_order, SavedFilter.created_at)
                )
            )
            .scalars()
            .all()
        )
        return [
            SubjectChip(
                id=row.id,
                label=row.label,
                subject=row.subject,
                seen=multiplexer().seen(server_id, row.subject),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------- plumbing

    async def _client(self, server_id: uuid.UUID) -> Client:
        """The live NATS client, or a problem detail.

        `_connect` already refuses a connection with no client, but that guarantee
        does not survive the return type, so publish and request go through here
        and get a `Client` rather than a `Client | None` they would have to unwrap.
        """
        conn = await self._connect(server_id)
        assert conn.nc is not None
        return conn.nc

    async def _connect(self, server_id: uuid.UUID) -> ManagedConnection:
        try:
            conn = await self._connections.ensure(server_id)
        except Exception as exc:
            raise NatsProblem.of(exc) from exc
        if conn.nc is None:
            raise NatsProblem.of(nats_errors.ConnectionClosedError())
        return conn

    def _decode_hook(self, server_id: uuid.UUID) -> DecodeHook:
        """Bound to one server, so the multiplexer never has to know a server id."""

        async def hook(payload: bytes, subject: str, headers: Mapping[str, str]) -> Decoded:
            async with self._session_factory() as session:
                repo = SchemaRepository(session)
                index = await REGISTRY.index(repo)
                rules = await REGISTRY.rules(repo, server_id)
                decoded = chain.decode(payload, subject, dict(headers), rules, index)
                matched = rules.match(subject) if subject else None
                SAMPLES.record(
                    decoded, subject, server_id=server_id, rule_id=matched.id if matched else None
                )
                return decoded

        return hook


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 1)


def _b64decode(value: str, field: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationException(detail=f"{field} is not valid base64.") from exc
