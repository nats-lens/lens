"""Protobuf wire format, read without a schema.

Step 5 of the chain and the reason it can promise never to fail: every byte
sequence has *some* reading as tag/value pairs, and where that reading runs out
the walker stops and says so rather than raising. No protobuf library is imported
here -- the wire format is eight lines of specification, and depending on the
runtime for it would mean the fallback needs the very descriptor it is standing in
for.

The renderings are the design's fallback rows verbatim:

    varint 214
    fixed32 21.4 - 0x41ab3333
    len 11 "device-4471"

Bounded on purpose. A 16 MB payload of one-byte fields would otherwise produce
eight million rows for a panel that shows six, so the walk stops at MAX_FIELDS and
reports `truncated`. Nesting is one level deep (a LEN field that is itself a
message), which also means the walk is iterative and cannot exhaust the stack.
"""

from __future__ import annotations

import math
import struct
from typing import Final

import msgspec

from nats_lens.codec.schemas import WireField, WireType

MAX_FIELDS: Final = 512
"""Rows past this are not information, they are a memory leak with a scrollbar."""

MAX_DEPTH: Final = 1
"""A LEN field may be walked as a nested message, but its children are not."""

_MAX_VARINT_BYTES: Final = 10
_STRING_PREVIEW: Final = 160
_HEX_PREVIEW: Final = 32
_ELLIPSIS: Final = "\u2026"
_MIDDOT: Final = "\u00b7"

# Floats that came out of a fixed32/fixed64 field only look like floats inside a
# plausible magnitude range. A small integer read as a float32 gives 1.4e-42,
# which is noise; showing the integer is the honest reading of those bytes.
_FLOAT_MIN: Final = 1e-6
_FLOAT_MAX: Final = 1e12

_ALLOWED_CONTROLS: Final = frozenset("\t\n\r")

_WIRE_TYPES: Final[dict[int, WireType]] = {
    0: WireType.VARINT,
    1: WireType.FIXED64,
    2: WireType.LEN,
    3: WireType.GROUP_START,
    4: WireType.GROUP_END,
    5: WireType.FIXED32,
}


class WireParse(msgspec.Struct, frozen=True):
    """What the walk recovered, and how far it got."""

    fields: tuple[WireField, ...] = ()
    complete: bool = True
    """True when every byte was consumed as a well-formed field."""
    truncated: bool = False
    """True when the walk stopped at MAX_FIELDS with bytes still to read."""
    warnings: tuple[str, ...] = ()

    @property
    def looks_like_protobuf(self) -> bool:
        """A clean full-payload parse of at least one field.

        Not proof -- plenty of byte strings parse by accident -- but it is the
        difference between calling a payload `protobuf` and calling it `binary`.
        """
        return self.complete and bool(self.fields)


def parse(payload: bytes, *, max_fields: int = MAX_FIELDS, max_depth: int = MAX_DEPTH) -> WireParse:
    """Walk `payload` as protobuf tag/value pairs. Never raises."""
    return _parse(memoryview(payload), max_fields=max_fields, depth=0, max_depth=max_depth)


def as_text(data: bytes) -> str | None:
    """The UTF-8 reading of `data`, if it is one a person would recognise as text.

    Strict UTF-8 alone is too generous: a nested message such as `\\n\\x03abc`
    decodes cleanly but is not text, so control characters other than tab and the
    two newline bytes disqualify it.
    """
    try:
        text = data.decode()
    except UnicodeDecodeError:
        return None
    for ch in text:
        if ch.isprintable() or ch in _ALLOWED_CONTROLS:
            continue
        return None
    return text


def field_numbers(payload: bytes) -> frozenset[int]:
    """The field numbers physically present in `payload`.

    Used by the schema decoder: proto3 has no presence for scalar fields, so a
    `paid = false` that a publisher really did put on the wire is invisible to
    `ListFields`. The wire knows, so the wire is asked.
    """
    result = parse(payload, max_fields=MAX_FIELDS)
    return frozenset(f.field_number for f in result.fields)


def _parse(buf: memoryview, *, max_fields: int, depth: int, max_depth: int) -> WireParse:
    fields: list[WireField] = []
    warnings: list[str] = []
    pos = 0
    size = len(buf)

    while pos < size:
        if len(fields) >= max_fields:
            return WireParse(
                fields=tuple(fields),
                complete=False,
                truncated=True,
                warnings=(*warnings, f"stopped after {max_fields} fields"),
            )

        start = pos
        tag = _read_varint(buf, pos, size)
        if tag is None:
            warnings.append(f"truncated varint at offset {start}")
            return WireParse(fields=tuple(fields), complete=False, warnings=tuple(warnings))
        tag_value, pos = tag

        field_number = tag_value >> 3
        wire_type = _WIRE_TYPES.get(tag_value & 0x07)
        if field_number == 0 or wire_type is None:
            warnings.append(f"invalid tag 0x{tag_value:x} at offset {start}")
            return WireParse(fields=tuple(fields), complete=False, warnings=tuple(warnings))

        field = _read_value(buf, pos, size, field_number, wire_type, depth, max_depth)
        if field is None:
            warnings.append(f"truncated {wire_type.value} field {field_number} at offset {start}")
            return WireParse(fields=tuple(fields), complete=False, warnings=tuple(warnings))
        parsed, pos = field
        fields.append(parsed)

    return WireParse(fields=tuple(fields), complete=True, warnings=tuple(warnings))


def _read_value(
    buf: memoryview,
    pos: int,
    size: int,
    field_number: int,
    wire_type: WireType,
    depth: int,
    max_depth: int,
) -> tuple[WireField, int] | None:
    if wire_type is WireType.VARINT:
        read = _read_varint(buf, pos, size)
        if read is None:
            return None
        value, end = read
        raw = bytes(buf[pos:end])
        return (
            WireField(
                field_number=field_number,
                wire_type=wire_type,
                render=f"varint {_varint_render(value)}",
                raw_hex=raw.hex(),
            ),
            end,
        )

    if wire_type is WireType.FIXED64:
        end = pos + 8
        if end > size:
            return None
        raw = bytes(buf[pos:end])
        value = struct.unpack("<Q", raw)[0]
        number = _fixed_number(struct.unpack("<d", raw)[0], value, raw, single=False)
        return (
            WireField(
                field_number=field_number,
                wire_type=wire_type,
                render=f"fixed64 {number} {_MIDDOT} 0x{value:016x}",
                raw_hex=raw.hex(),
            ),
            end,
        )

    if wire_type is WireType.FIXED32:
        end = pos + 4
        if end > size:
            return None
        raw = bytes(buf[pos:end])
        value = struct.unpack("<I", raw)[0]
        number = _fixed_number(struct.unpack("<f", raw)[0], value, raw, single=True)
        return (
            WireField(
                field_number=field_number,
                wire_type=wire_type,
                render=f"fixed32 {number} {_MIDDOT} 0x{value:08x}",
                raw_hex=raw.hex(),
            ),
            end,
        )

    if wire_type is WireType.LEN:
        read = _read_varint(buf, pos, size)
        if read is None:
            return None
        length, body = read
        end = body + length
        if length < 0 or end > size:
            return None
        data = bytes(buf[body:end])
        render, nested = _render_len(data, depth, max_depth)
        return (
            WireField(
                field_number=field_number,
                wire_type=wire_type,
                render=render,
                raw_hex=data[:_HEX_PREVIEW].hex(),
                nested=nested,
            ),
            end,
        )

    # Groups are deprecated and the runtime has not emitted them since proto2.
    # Their members are ordinary fields between the two markers, so the walk keeps
    # going and renders them flat rather than pretending to a structure the bytes
    # do not carry unambiguously.
    marker = "start" if wire_type is WireType.GROUP_START else "end"
    return (
        WireField(
            field_number=field_number,
            wire_type=wire_type,
            render=f"group {marker}",
            raw_hex="",
        ),
        pos,
    )


def _render_len(data: bytes, depth: int, max_depth: int) -> tuple[str, tuple[WireField, ...]]:
    text = as_text(data)
    if text is not None:
        return f'len {len(data)} "{_escape(text)}"', ()

    if depth < max_depth:
        nested = _parse(
            memoryview(data), max_fields=MAX_FIELDS, depth=depth + 1, max_depth=max_depth
        )
        if nested.looks_like_protobuf:
            plural = "" if len(nested.fields) == 1 else "s"
            return (
                f"len {len(data)} {_MIDDOT} message, {len(nested.fields)} field{plural}",
                nested.fields,
            )

    return f"len {len(data)} {_hex_preview(data)}", ()


def _read_varint(buf: memoryview, pos: int, size: int) -> tuple[int, int] | None:
    value = 0
    shift = 0
    read = 0
    while pos < size:
        byte = buf[pos]
        pos += 1
        read += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        if read >= _MAX_VARINT_BYTES:
            return None
        shift += 7
    return None


def _varint_render(value: int) -> str:
    # A negative int32/int64 is transmitted as its two's-complement in ten bytes,
    # so the unsigned reading is a 20-digit number that means -1. Show both.
    if value >= 1 << 63:
        return f"{value} {_MIDDOT} {value - (1 << 64)}"
    return str(value)


def _fixed_number(as_float: float, as_uint: int, raw: bytes, *, single: bool) -> str:
    if not math.isfinite(as_float):
        return str(as_uint)
    if as_float != 0.0 and not (_FLOAT_MIN <= abs(as_float) < _FLOAT_MAX):
        return str(as_uint)
    if as_float == 0.0 and as_uint != 0:
        return str(as_uint)
    return _shortest_float(as_float, raw, single=single)


def _shortest_float(value: float, raw: bytes, *, single: bool) -> str:
    """The fewest digits that still round-trip to the same bytes.

    Python has no float32, so `struct` widens 0x41ab3333 to 21.399999618530273 and
    printing that would be a lie about what the publisher sent. The shortest form
    that packs back to the original four bytes is 21.4, which is what it wrote.
    """
    if not single:
        return repr(value)
    for precision in range(1, 10):
        candidate = f"{value:.{precision}g}"
        if struct.pack("<f", float(candidate)) == raw:
            return candidate
    return repr(value)


def _escape(text: str) -> str:
    clipped = text[:_STRING_PREVIEW]
    escaped = clipped.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return escaped + _ELLIPSIS if len(text) > _STRING_PREVIEW else escaped


def _hex_preview(data: bytes) -> str:
    head = data[:_HEX_PREVIEW].hex()
    return f"0x{head}{_ELLIPSIS}" if len(data) > _HEX_PREVIEW else f"0x{head}"
