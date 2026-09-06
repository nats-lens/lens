"""The two 'JetStream not enabled' cases have two different fixes."""

from __future__ import annotations

import pytest
from nats.js import errors as js_errors

from nats_lens.conn.connection import ManagedConnection
from nats_lens.provenance import Reason

pytestmark = pytest.mark.unit


def _reason(exc: Exception) -> Reason:
    got = ManagedConnection._jetstream_unavailable(object.__new__(ManagedConnection), exc)
    assert got.unavailable is not None
    return got.unavailable.reason


def test_an_account_without_jetstream_is_not_a_server_without_it() -> None:
    """NATS says which; telling someone to restart with `-js` when the server
    already runs it sends them the wrong way."""
    exc = js_errors.ServiceUnavailableError()
    exc.description = "JetStream not enabled for account"
    assert _reason(exc) is Reason.JETSTREAM_NOT_ENABLED_FOR_ACCOUNT


def test_a_server_without_jetstream_still_reads_as_the_server() -> None:
    exc = js_errors.ServiceUnavailableError()
    exc.description = "JetStream not enabled"
    assert _reason(exc) is Reason.JETSTREAM_NOT_ENABLED
