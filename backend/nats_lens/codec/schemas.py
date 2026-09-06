"""The decoding contract, shared by the Core screen, JetStream messages, KV and Advisories.

FROZEN CONTRACT -- see domain/common.py.
"""


# instance rather than sharing them, so `= {}` here is safe. Verified.

from __future__ import annotations

from enum import StrEnum

import msgspec


class Codec(StrEnum):
    JSON = "json"
    MSGPACK = "msgpack"
    PROTOBUF = "protobuf"
    TEXT = "text"
    BINARY = "binary"
    EMPTY = "empty"


class ResolvedBy(StrEnum):
    """Which step of the five-step chain produced the answer.

    The chain always terminates: WIRE is the floor, and it can decode any bytes.
    """

    HEADER = "header"
    """1. A `Nats-Msg-Type` header. A publisher naming its own type wins over everything."""
    SUBJECT_RULE = "subject_rule"
    """2. The most specific registered subject pattern."""
    CONTENT_TYPE = "content_type"
    """3. A `Content-Type` header."""
    SNIFF = "sniff"
    """4. Shape of the bytes: JSON, MessagePack, UTF-8 text."""
    WIRE = "wire"
    """5. Raw protobuf wire format -- field numbers and wire types, no schema."""
    CHOSEN = "chosen"
    """Not a step at all: a person picked the type in the inspector. Kept distinct
    from the five so a reading nobody would get on the wire is never mistaken for
    one the chain produced."""


class WireType(StrEnum):
    VARINT = "varint"
    FIXED64 = "fixed64"
    LEN = "len"
    FIXED32 = "fixed32"
    GROUP_START = "group_start"
    GROUP_END = "group_end"


class WireField(msgspec.Struct, frozen=True):
    """One field recovered without a schema, as the design's fallback rows show it."""

    field_number: int
    wire_type: WireType
    render: str
    """`varint 214`, `fixed32 21.4 - 0x41ab3333`, `len 11 "device-4471"`."""
    raw_hex: str
    nested: tuple[WireField, ...] = ()
    """Populated when a LEN field itself parses cleanly as a message."""


class DecodedField(msgspec.Struct, frozen=True):
    """One field of a schema-decoded message."""

    name: str
    field_number: int
    type_name: str
    value: str
    repeated: bool = False


class Decoded(msgspec.Struct, frozen=True):
    """The result of running the chain. Never an error -- step 5 always answers."""

    codec: Codec
    resolved_by: ResolvedBy
    type_name: str | None = None
    """The protobuf message type, when one was resolved."""
    fields: tuple[DecodedField, ...] = ()
    wire_fields: tuple[WireField, ...] = ()
    text: str | None = None
    """JSON / MessagePack / plain text rendered for display."""
    truncated: bool = False
    warnings: tuple[str, ...] = ()
    unmapped_subject: str | None = None
    """Set when the chain fell to WIRE -- the subject the UI offers to map."""


class DecodePreview(msgspec.Struct, frozen=True):
    """Decode arbitrary bytes without publishing anything. Used by the Schemas screen."""

    subject: str
    payload_b64: str
    headers: dict[str, str] = {}
    type_full_name: str | None = None
    """Decode as this type instead of running the chain.

    For the inspector, where an operator reads a message whose subject no rule
    claims and wants to try a type against it. `resolved_by` comes back as
    `chosen`, so the answer is never confused with one the chain reached."""
