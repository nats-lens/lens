"""Recognising our own publish in the stream coming back.

NATS echoes a publisher's message to its own matching subscriptions, so without
this the Core transcript would show every UI publish twice -- once as OUT, then
again moments later as an indistinguishable IN.

The fingerprint that does the recognising is a hash of the whole payload, and it
used to run on every inbound message. Nothing is ever pending unless someone has
published from the UI in the last few hundred messages, so on a firehose being
watched that was a digest per message to answer "no".
"""

from __future__ import annotations

import uuid

import pytest

from nats_lens.conn.multiplex import PENDING_OUT_SIZE, _fingerprint, _Subscription

pytestmark = pytest.mark.unit


def _sub() -> _Subscription:
    return _Subscription(uuid.uuid4(), "orders.>", None)


def test_our_own_publish_is_recognised_once() -> None:
    sub = _sub()
    sub.mark_pending_out(_fingerprint("orders.new", b"payload", {"k": "v"}))

    assert sub.is_own_echo("orders.new", b"payload", {"k": "v"}) is True
    # Consumed: a second, genuinely different message on the same subject is not
    # ours, and must appear in the transcript.
    assert sub.is_own_echo("orders.new", b"payload", {"k": "v"}) is False


def test_a_message_we_did_not_publish_is_never_ours() -> None:
    sub = _sub()
    sub.mark_pending_out(_fingerprint("orders.new", b"payload", {}))

    assert sub.is_own_echo("orders.new", b"different", {}) is False
    assert sub.is_own_echo("orders.other", b"payload", {}) is False
    # Headers are part of the identity.
    assert sub.is_own_echo("orders.new", b"payload", {"k": "v"}) is False


def test_nothing_pending_hashes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The optimisation itself: no pending publish, no digest."""
    import nats_lens.conn.multiplex as multiplex

    def explode(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("hashed a payload with nothing pending")

    monkeypatch.setattr(multiplex, "_fingerprint", explode)
    assert _sub().is_own_echo("orders.new", b"payload", {}) is False


def test_the_pending_ring_is_bounded() -> None:
    """A publisher whose messages are never echoed must not leak."""
    sub = _sub()
    for i in range(PENDING_OUT_SIZE * 2):
        sub.mark_pending_out(_fingerprint("orders.new", str(i).encode(), {}))

    assert len(sub._pending_out) == PENDING_OUT_SIZE
    assert len(sub._pending_out_set) == PENDING_OUT_SIZE
    # The oldest fell out; the newest is still recognised.
    assert sub.is_own_echo("orders.new", b"0", {}) is False
    assert sub.is_own_echo("orders.new", str(PENDING_OUT_SIZE * 2 - 1).encode(), {}) is True
