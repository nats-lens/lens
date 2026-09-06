"""Reaching NATS from a domain service.

Streams, key-value buckets and object stores are three faces of one JetStream
account, so all three services need the same four things before they can do
anything: a connection, proof that JetStream is actually reachable on it, the
`js`/`jsm` handles, and a promise that a raw nats-py exception will leave as an
RFC 9457 body rather than a 500.

That preamble used to live in `domain/jetstream/service.py`, which meant the KV
and object-store services imported their plumbing from a sibling domain. It is
here instead: shared by all three, owned by none.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator, Mapping
from enum import Enum
from typing import NamedTuple

from litestar.exceptions import HTTPException, NotFoundException
from nats import errors as nats_errors
from nats.js import JetStreamContext, JetStreamManager, api
from sqlalchemy.ext.asyncio import AsyncSession

from nats_lens.codec.chain import decode
from nats_lens.codec.schemas import Decoded
from nats_lens.conn.connection import ManagedConnection
from nats_lens.conn.errors import NatsProblem
from nats_lens.conn.manager import ConnectionManager, UnknownServer
from nats_lens.domain.common import ProblemDetail
from nats_lens.domain.protoschemas.repository import SchemaRepository
from nats_lens.domain.protoschemas.service import REGISTRY
from nats_lens.provenance import Reason, Unavailable

PAGE_SIZE = 256
"""The JetStream API's own page size for STREAM.LIST and CONSUMER.LIST."""

_UNAVAILABLE_STATUS: dict[Reason, int] = {
    Reason.NOT_CONNECTED: 503,
    Reason.JETSTREAM_NOT_ENABLED: 503,
    Reason.JETSTREAM_NOT_ENABLED_FOR_ACCOUNT: 503,
    Reason.NOT_SUPPORTED_BY_SERVER: 502,
}


def unavailable_problem(unavailable: Unavailable, *, instance: str | None = None) -> NatsProblem:
    """The fix sentence a `Sourced` absence would carry, as an HTTP problem.

    Streams, consumers, buckets and objects are never `Sourced`; they are always
    `jetstream` provenance by the frozen contract, so unavailability here is the
    whole request failing rather than one field going missing inside a 200.
    """
    return NatsProblem(
        ProblemDetail(
            type=f"/problems/{unavailable.reason.value}",
            title="JetStream is not available",
            status=_UNAVAILABLE_STATUS.get(unavailable.reason, 503),
            detail=unavailable.fix,
            instance=instance,
            nats_error=None,
        )
    )


@contextlib.asynccontextmanager
async def translate_nats_errors(instance: str | None = None) -> AsyncIterator[None]:
    """The one place a raw nats-py exception becomes the RFC 9457 body
    `conn/errors.py` already knows how to write.

    Litestar has no application-wide handler for these -- `app.py` is frozen to
    routing only -- so every service method that talks to NATS wraps its body in
    this rather than leaving a bare `NotFoundError` to fall through as a 500.
    """
    try:
        yield
    except HTTPException:
        raise
    except UnknownServer as exc:
        raise NotFoundException(detail=str(exc)) from exc
    except (nats_errors.Error, OSError, TimeoutError) as exc:
        raise NatsProblem.of(exc, instance=instance) from exc


async def connected(connections: ConnectionManager, server_id: uuid.UUID) -> ManagedConnection:
    """Every handler's first step. `translate_nats_errors` turns a failure here
    into the same problem detail a bad request further down would produce."""
    return await connections.ensure(server_id)


async def require_jetstream(conn: ManagedConnection) -> None:
    """Raise the curated reason, if JetStream itself is not reachable on `conn`.

    Reuses `ManagedConnection.jetstream_account()` -- already the Servers screen's
    way of asking this exact question, cached for a couple of seconds -- rather
    than inventing a second way to notice a disabled or disconnected account.
    """
    account = await conn.jetstream_account()
    if account.unavailable is not None:
        raise unavailable_problem(account.unavailable)


class JetStream(NamedTuple):
    """A connection that has been checked, with both handles already narrowed.

    `context` and `manager` are set and cleared together by `ManagedConnection`,
    so once JetStream is known reachable neither can be None -- which is why the
    three `assert`s that used to open every handler live here once instead.
    """

    conn: ManagedConnection
    context: JetStreamContext
    manager: JetStreamManager


@contextlib.asynccontextmanager
async def jetstream(
    connections: ConnectionManager, server_id: uuid.UUID, instance: str | None = None
) -> AsyncIterator[JetStream]:
    """Connect, check, hand over both handles -- inside the error translation.

    The order is the point: `connected()` runs *within* `translate_nats_errors`,
    so a server that is registered but unreachable produces the same 503 problem
    detail as a failure further in, rather than an untranslated 500.
    """
    async with translate_nats_errors(instance):
        conn = await connected(connections, server_id)
        await require_jetstream(conn)
        assert conn.js is not None
        assert conn.jsm is not None
        yield JetStream(conn, conn.js, conn.jsm)


async def iter_stream_infos(jsm: JetStreamManager) -> AsyncIterator[api.StreamInfo]:
    """Every stream, paging past the 256-stream wall `streams_info_iterator`
    itself does not cross -- it hands back one page, not the whole list."""
    offset = 0
    while True:
        batch = list(await jsm.streams_info_iterator(offset=offset))
        if not batch:
            return
        for info in batch:
            yield info
        if len(batch) < PAGE_SIZE:
            return
        offset += len(batch)


async def decode_payload(
    session: AsyncSession,
    server_id: uuid.UUID | None,
    payload: bytes,
    subject: str,
    headers: Mapping[str, str],
) -> Decoded:
    """Run the five-step chain, with this server's rules and the shared descriptor index."""
    repo = SchemaRepository(session)
    index = await REGISTRY.index(repo)
    rules = await REGISTRY.rules(repo, server_id)
    return decode(payload, subject, headers, rules, index)


def enum_value(raw: object, default: str) -> str:
    """The string behind a nats-py config field, whichever form it arrives in.

    nats-py types these as enums, but its `from_response` parsers leave them as
    plain strings when a config comes back off the wire -- so the same attribute
    is an enum on a config we built and a string on one the server sent. Reading
    `.value` unconditionally works right up until the first real server replies.
    """
    if raw is None:
        return default
    return raw.value if isinstance(raw, Enum) else str(raw)
