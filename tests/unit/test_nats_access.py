"""The preamble every JetStream-backed service runs before it touches NATS.

Two things are pinned here. The first is that reaching the server happens
*inside* the error translation: the object-store service used to connect first
and translate afterwards, so a registered-but-unreachable server produced an
untranslated 500 instead of the 503 problem detail every sibling endpoint
returns for the same failure.

The second is that `js` and `jsm` come back already narrowed, which is what
retired the twenty-six four-line preambles that used to open every handler.
"""

from __future__ import annotations

import uuid
from typing import cast

import pytest
from litestar.exceptions import NotFoundException
from nats import errors as nats_errors

from nats_lens.conn.errors import NatsProblem
from nats_lens.conn.manager import ConnectionManager, UnknownServer
from nats_lens.domain.nats_access import jetstream

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


class _Connections:
    """Just enough `ConnectionManager` for `jetstream()` to run against."""

    def __init__(self, outcome: object) -> None:
        self._outcome = outcome

    async def ensure(self, server_id: uuid.UUID) -> object:
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


def _manager(outcome: object) -> ConnectionManager:
    """`jetstream()` only ever calls `ensure`, so a stub standing in for the
    whole manager is honest -- the cast is what says so to the type checker."""
    return cast("ConnectionManager", _Connections(outcome))


class _Account:
    unavailable = None


class _Conn:
    """A connection that is up, with JetStream reachable."""

    js = object()
    jsm = object()

    async def jetstream_account(self) -> _Account:
        return _Account()


async def test_an_unreachable_server_is_a_problem_detail_not_a_500() -> None:
    """The regression: the connect has to happen inside the translation."""
    connections = _manager(nats_errors.NoServersError())

    with pytest.raises(NatsProblem) as caught:
        async with jetstream(connections, uuid.uuid4(), "/objects"):
            pass

    assert caught.value.status_code == 503
    assert caught.value.problem.nats_error == "nats.errors.NoServersError"
    assert caught.value.problem.instance == "/objects"


async def test_an_unregistered_server_is_a_404() -> None:
    connections = _manager(UnknownServer(uuid.uuid4()))

    with pytest.raises(NotFoundException):
        async with jetstream(connections, uuid.uuid4(), "/objects"):
            pass


async def test_both_handles_arrive_narrowed() -> None:
    """`js` and `jsm` are set and cleared together, so one check covers both."""
    conn = _Conn()
    connections = _manager(conn)

    async with jetstream(connections, uuid.uuid4()) as js:
        assert js.conn is conn
        assert js.context is conn.js
        assert js.manager is conn.jsm
