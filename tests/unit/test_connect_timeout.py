"""Opening a connection has to give up.

nats-py retries the *initial* dial for as long as `max_reconnect_attempts`
allows, and the default here is -1: forever. `connect_timeout` bounds one TCP
attempt, not the sequence. So a server that is merely unreachable made every
request that touched it hang indefinitely -- editing it, or pressing Connect --
with no error and no way back.

Reconnection *after* a successful connect is still unlimited on purpose. It is
only the first dial that has an HTTP request waiting on the other end.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from nats import errors as nats_errors
from nats.aio.client import Client as NATS

from nats_lens.conn.auth import AuthSpec, TlsSpec
from nats_lens.conn.connection import ConnectionSpec, ManagedConnection
from nats_lens.domain.servers.schemas import AuthMode, ConnectionState

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


def _spec(urls: tuple[str, ...]) -> ConnectionSpec:
    return ConnectionSpec(
        server_id=uuid.uuid4(),
        name="unreachable",
        urls=urls,
        auth=AuthSpec(mode=AuthMode.NONE),
        tls=TlsSpec(),
    )


async def test_open_gives_up_instead_of_dialling_forever() -> None:
    """A connector that never returns must not become a request that never returns."""

    async def never_answers(**_: object) -> NATS:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    conn = ManagedConnection(
        _spec(("nats://192.0.2.1:4222",)),
        connector=never_answers,
        connect_timeout=0.05,
    )

    async with asyncio.timeout(5):
        with pytest.raises(nats_errors.NoServersError):
            await conn.open()

    assert conn.state is ConnectionState.ERROR
    # nats-py hardcodes NoServersError's __str__, so the useful half -- how long we
    # waited and across how many URLs -- has to survive into `last_error` or the
    # screen just says "no servers available" and leaves the user guessing.
    assert conn.last_error is not None
    assert "NoServersError" in conn.last_error
    assert "answered within" in conn.last_error


async def test_the_budget_scales_with_the_number_of_seeds() -> None:
    """Each failover URL deserves an attempt, so more seeds means a longer budget.

    Asserted through observed duration rather than by reading the constant, so
    the test still means something if the arithmetic changes.
    """
    attempts: list[float] = []

    async def never_answers(**_: object) -> NATS:
        attempts.append(asyncio.get_running_loop().time())
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    one = ManagedConnection(_spec(("nats://a:4222",)), connector=never_answers, connect_timeout=0.1)
    three = ManagedConnection(
        _spec(("nats://a:4222", "nats://b:4222", "nats://c:4222")),
        connector=never_answers,
        connect_timeout=0.1,
    )

    loop = asyncio.get_running_loop()
    start = loop.time()
    with pytest.raises(nats_errors.NoServersError):
        await one.open()
    single = loop.time() - start

    start = loop.time()
    with pytest.raises(nats_errors.NoServersError):
        await three.open()
    triple = loop.time() - start

    assert triple > single, "three seeds should be given longer than one"


async def test_a_connector_that_fails_fast_still_reports_its_own_error() -> None:
    """The timeout must not swallow the real reason when there is one."""

    async def refused(**_: object) -> NATS:
        raise ConnectionRefusedError("[Errno 111] Connection refused")

    conn = ManagedConnection(_spec(("nats://a:4222",)), connector=refused, connect_timeout=1.0)
    with pytest.raises(ConnectionRefusedError):
        await conn.open()
    assert conn.state is ConnectionState.ERROR
    assert "refused" in (conn.last_error or "")


async def test_a_rejected_password_is_not_reported_as_an_unreachable_host() -> None:
    """The distinction the user actually needs.

    nats-py retries an authorization failure exactly like a refused connection,
    so the budget expires either way. Reporting "no servers answered" for a
    server that answered and said no sends the user to check the network when
    the problem is the password.
    """

    async def rejects_forever(*, error_cb=None, **_: object) -> NATS:
        while True:
            if error_cb is not None:
                await error_cb(nats_errors.Error("nats: 'Authorization Violation'"))
            await asyncio.sleep(0.01)

    conn = ManagedConnection(
        _spec(("nats://a:4222",)), connector=rejects_forever, connect_timeout=0.05
    )
    async with asyncio.timeout(5):
        with pytest.raises(nats_errors.NoServersError):
            await conn.open()

    assert conn.last_error is not None
    assert "Authorization Violation" in conn.last_error
    assert "gave up after" in conn.last_error


async def test_a_stale_error_is_not_blamed_on_the_next_attempt() -> None:
    """A previous failure must not be reported as the reason for this one."""

    async def refused(**_: object) -> NATS:
        raise ConnectionRefusedError("[Errno 111] Connection refused")

    conn = ManagedConnection(_spec(("nats://a:4222",)), connector=refused, connect_timeout=0.05)
    with pytest.raises(ConnectionRefusedError):
        await conn.open()
    assert "refused" in (conn.last_error or "")

    async def never_answers(**_: object) -> NATS:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    conn._connector = never_answers
    async with asyncio.timeout(5):
        with pytest.raises(nats_errors.NoServersError):
            await conn.open()
    assert "refused" not in (conn.last_error or ""), "the earlier failure leaked into this one"
