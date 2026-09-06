"""Step 4: what shape are these bytes.

Only reached when nobody said. The header did not name a type, no subject rule
claims the subject and there was no usable `Content-Type`, so all that is left is
the payload itself.

Sniffing is guessing, and the cost of a wrong guess is a message rendered as the
wrong thing, so the guesses here are deliberately narrower than the parsers they
use. msgspec will happily read `42` as JSON and the single byte `{` as a
MessagePack integer; either would let a protobuf payload be announced as
something it is not. A sniffed JSON or MessagePack payload therefore has to be an
object or an array -- the shape a message actually arrives in -- while step 3,
where a publisher set `Content-Type` and took responsibility, accepts whatever
the parser accepts.
"""

from __future__ import annotations

from typing import Any, Final

import msgspec

from nats_lens.codec.schemas import Codec

_JSON_OPENERS: Final = b"{["
_JSON_WHITESPACE: Final = b" \t\r\n"
_ALLOWED_CONTROLS: Final = frozenset("\t\n\r")


class Sniffed(msgspec.Struct, frozen=True):
    codec: Codec
    text: str


def sniff(payload: bytes) -> Sniffed | None:
    """The shape of `payload`, or None when only the wire walker can say."""
    if not payload:
        # Not text that happens to be zero characters long: a publish with no body
        # is a fact about the message, and the UI has an empty state for it.
        return Sniffed(codec=Codec.EMPTY, text="")

    if _opens_a_json_document(payload) and (rendered := render_json(payload)) is not None:
        return Sniffed(codec=Codec.JSON, text=rendered)

    if (rendered := render_msgpack(payload, containers_only=True)) is not None:
        return Sniffed(codec=Codec.MSGPACK, text=rendered)

    if (text := as_text(payload)) is not None:
        return Sniffed(codec=Codec.TEXT, text=text)

    return None


def render_json(payload: bytes) -> str | None:
    """Pretty-print `payload` as JSON, keeping the publisher's key order.

    `msgspec.json.format` reformats the bytes rather than round-tripping through
    Python objects, so nothing is reordered, no float is reprinted and a 64-bit
    integer that JavaScript could not hold survives intact.
    """
    try:
        return msgspec.json.format(payload, indent=2).decode()
    except msgspec.DecodeError, UnicodeDecodeError:
        return None


def render_msgpack(payload: bytes, *, containers_only: bool = False) -> str | None:
    """Decode MessagePack and render it as JSON for display."""
    try:
        value: Any = msgspec.msgpack.decode(payload)
    except msgspec.DecodeError, ValueError, MemoryError, RecursionError:
        return None
    if containers_only and not isinstance(value, dict | list):
        return None
    try:
        return msgspec.json.format(msgspec.json.encode(value), indent=2).decode()
    except msgspec.EncodeError, TypeError:
        # MessagePack carries types JSON has no spelling for -- extension types,
        # non-string map keys. Showing Python's repr beats showing nothing.
        return repr(value)


def as_text(payload: bytes) -> str | None:
    """UTF-8 text a person would recognise as text, or None."""
    try:
        text = payload.decode()
    except UnicodeDecodeError:
        return None
    for ch in text:
        if ch.isprintable() or ch in _ALLOWED_CONTROLS:
            continue
        return None
    return text


def _opens_a_json_document(payload: bytes) -> bool:
    for byte in payload[:64]:
        if byte in _JSON_WHITESPACE:
            continue
        return byte in _JSON_OPENERS
    return False
