"""The five-step decoding chain.

Given bytes off a subject, decide what they are. In order, and always
terminating:

    1. a `Nats-Msg-Type` header       a publisher naming its own type wins
    2. a subject rule                 the most specific registered pattern
    3. a `Content-Type` header        json, msgpack, x-protobuf, text
    4. the shape of the bytes         valid JSON, MessagePack, UTF-8 text
    5. raw protobuf wire format       the floor; it can read anything

Pure and synchronous on purpose. Nothing here touches the network, the database
or the event loop, which is what lets every branch above be tested against fixed
bytes rather than against a running server -- including the adversarial ones,
where the answer that matters is which step declined and why.

A step that could answer but did not -- a header naming a type no descriptor
declares, bytes that are not the type the rule promised -- leaves a warning and
hands on to the next step. `Decoded.resolved_by` therefore names the step that
actually produced the answer, never the step that was asked first, because the UI
renders it as "resolved by subject rule orders.new" and that has to be true.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

import msgspec

from nats_lens.codec import sniff as sniffer
from nats_lens.codec import wire
from nats_lens.codec.protobuf import DescriptorIndex, TypeMismatch
from nats_lens.codec.rules import RuleSet
from nats_lens.codec.schemas import Codec, Decoded, ResolvedBy

MSG_TYPE_HEADER: Final = "Nats-Msg-Type"
CONTENT_TYPE_HEADER: Final = "Content-Type"

MAX_TEXT_CHARS: Final = 64 * 1024
"""Enough for any message worth reading in a panel, and a bound on a 16 MB one."""

_ELLIPSIS: Final = "…"

_JSON_TYPES: Final = frozenset({"application/json", "text/json", "application/x-json"})
_MSGPACK_TYPES: Final = frozenset(
    {
        "application/msgpack",
        "application/x-msgpack",
        "application/x-messagepack",
        "application/vnd.msgpack",
    }
)
_PROTOBUF_TYPES: Final = frozenset(
    {
        "application/protobuf",
        "application/x-protobuf",
        "application/vnd.google.protobuf",
        "application/octet-stream+protobuf",
    }
)
_TYPE_PARAMETERS: Final = ("messagetype", "message-type", "proto", "type")
"""How the three common conventions spell the type name in a Content-Type."""


def decode(
    payload: bytes,
    subject: str,
    headers: Mapping[str, str],
    rules: RuleSet,
    index: DescriptorIndex,
) -> Decoded:
    """Run the chain. Always answers -- step 5 can read any byte sequence."""
    warnings: list[str] = []

    if (result := _from_header(payload, headers, index, warnings)) is not None:
        return result
    if (result := _from_subject_rule(payload, subject, rules, index, warnings)) is not None:
        return result

    content_type, parameters = _content_type(headers)
    result = _from_content_type(payload, content_type, parameters, index, warnings)
    if result is not None:
        return result

    # A publisher who said `application/x-protobuf` has ruled out JSON and text.
    # Sniffing anyway would only invent a wrong answer, so skip to the wire.
    if not _is_protobuf(content_type) and (result := _from_sniff(payload, warnings)) is not None:
        return result

    return _from_wire(payload, subject, warnings)


# ------------------------------------------------------------------- the steps


def _from_header(
    payload: bytes, headers: Mapping[str, str], index: DescriptorIndex, warnings: list[str]
) -> Decoded | None:
    """1. `Nats-Msg-Type`. The publisher said what it sent."""
    type_name = _header(headers, MSG_TYPE_HEADER)
    if not type_name or type_name == "-" or type_name == "—":
        return None
    return _decode_as(payload, type_name, ResolvedBy.HEADER, index, warnings, via=MSG_TYPE_HEADER)


def _from_subject_rule(
    payload: bytes,
    subject: str,
    rules: RuleSet,
    index: DescriptorIndex,
    warnings: list[str],
) -> Decoded | None:
    """2. The most specific registered pattern that claims this subject."""
    rule = rules.match(subject)
    if rule is None:
        return None
    return _decode_as(
        payload,
        rule.type_full_name,
        ResolvedBy.SUBJECT_RULE,
        index,
        warnings,
        via=f"subject rule {rule.pattern}",
    )


def _from_content_type(
    payload: bytes,
    content_type: str | None,
    parameters: dict[str, str],
    index: DescriptorIndex,
    warnings: list[str],
) -> Decoded | None:
    """3. `Content-Type`. Names the codec, and occasionally the type as well."""
    if content_type is None:
        return None

    if content_type in _JSON_TYPES or content_type.endswith("+json"):
        rendered = sniffer.render_json(payload)
        if rendered is None:
            warnings.append(
                f"{CONTENT_TYPE_HEADER} says {content_type} but the payload is not valid JSON."
            )
            return None
        return _text_result(Codec.JSON, ResolvedBy.CONTENT_TYPE, rendered, warnings)

    if content_type in _MSGPACK_TYPES or content_type.endswith("+msgpack"):
        rendered = sniffer.render_msgpack(payload)
        if rendered is None:
            warnings.append(
                f"{CONTENT_TYPE_HEADER} says {content_type} but the payload is not valid "
                "MessagePack."
            )
            return None
        return _text_result(Codec.MSGPACK, ResolvedBy.CONTENT_TYPE, rendered, warnings)

    if _is_protobuf(content_type):
        named = _named_type(parameters)
        if named is None:
            # Knowing it is protobuf is not knowing which message it is. The design
            # shows exactly this case as "no schema matches this subject", so the
            # chain falls to the wire walker rather than guessing.
            return None
        return _decode_as(
            payload,
            named,
            ResolvedBy.CONTENT_TYPE,
            index,
            warnings,
            via=f"{CONTENT_TYPE_HEADER} {content_type}",
        )

    if content_type.startswith("text/"):
        text = sniffer.as_text(payload)
        if text is None:
            warnings.append(
                f"{CONTENT_TYPE_HEADER} says {content_type} but the payload is not valid UTF-8."
            )
            return None
        return _text_result(Codec.TEXT, ResolvedBy.CONTENT_TYPE, text, warnings)

    warnings.append(f"{CONTENT_TYPE_HEADER} {content_type} names a codec nats-lens cannot read.")
    return None


def _from_sniff(payload: bytes, warnings: list[str]) -> Decoded | None:
    """4. The shape of the bytes, when nobody said."""
    sniffed = sniffer.sniff(payload)
    if sniffed is None:
        return None
    return _text_result(sniffed.codec, ResolvedBy.SNIFF, sniffed.text, warnings)


def _from_wire(payload: bytes, subject: str, warnings: list[str]) -> Decoded:
    """5. Field numbers and wire types. The floor, and it never fails."""
    if not payload:
        # Reachable only when a header claimed a codec: a body-less publish is a
        # fact about the message, not a subject the operator has failed to map.
        return Decoded(codec=Codec.EMPTY, resolved_by=ResolvedBy.WIRE, warnings=tuple(warnings))

    parsed = wire.parse(payload)
    return Decoded(
        codec=Codec.PROTOBUF if parsed.looks_like_protobuf else Codec.BINARY,
        resolved_by=ResolvedBy.WIRE,
        wire_fields=parsed.fields,
        truncated=parsed.truncated,
        warnings=(*warnings, *parsed.warnings),
        unmapped_subject=subject or None,
    )


# ------------------------------------------------------------------- machinery


def decode_as_type(
    payload: bytes, type_full_name: str, index: DescriptorIndex, subject: str = ""
) -> Decoded:
    """Read the bytes as one named type, whatever the chain would have said.

    For the inspector's "decode as" picker. Falls through to the wire-format walk
    when the type is unknown or the bytes do not fit it, because that is still a
    true reading of the payload -- and an operator trying a type against an
    unmapped subject needs to see that it did not fit, not an error page.
    """
    warnings: list[str] = []
    chosen = _decode_as(payload, type_full_name, ResolvedBy.CHOSEN, index, warnings, via="chosen")
    if chosen is not None:
        return chosen
    fallback = _from_wire(payload, subject, warnings)
    return _with_warning(
        fallback,
        f"{type_full_name} did not fit these bytes; showing the raw wire format instead.",
    )


def _with_warning(decoded: Decoded, note: str) -> Decoded:
    return msgspec.structs.replace(decoded, warnings=(*decoded.warnings, note))


def _decode_as(
    payload: bytes,
    type_name: str,
    resolved_by: ResolvedBy,
    index: DescriptorIndex,
    warnings: list[str],
    *,
    via: str,
) -> Decoded | None:
    """Decode as a named protobuf type, or explain why the step is handing on."""
    try:
        decoded = index.decode_as(type_name, payload)
    except TypeMismatch as exc:
        warnings.append(f"{via} names {type_name}, but these bytes are not that message: {exc}")
        return None

    if decoded is None:
        warnings.append(
            f"{via} names {type_name}, but no registered descriptor declares it. "
            "Upload the .proto or its FileDescriptorSet on the Schemas screen."
        )
        return None

    return Decoded(
        codec=Codec.PROTOBUF,
        resolved_by=resolved_by,
        type_name=type_name,
        fields=decoded.fields,
        warnings=(*warnings, *decoded.warnings),
    )


def _text_result(codec: Codec, resolved_by: ResolvedBy, text: str, warnings: list[str]) -> Decoded:
    clipped = text[:MAX_TEXT_CHARS]
    truncated = len(text) > MAX_TEXT_CHARS
    return Decoded(
        codec=codec,
        resolved_by=resolved_by,
        text=clipped + _ELLIPSIS if truncated else clipped,
        truncated=truncated,
        warnings=tuple(warnings),
    )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """NATS headers are HTTP headers, and HTTP header names are case-insensitive."""
    if (value := headers.get(name)) is not None:
        return value.strip() or None
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value.strip() or None
    return None


def _content_type(headers: Mapping[str, str]) -> tuple[str | None, dict[str, str]]:
    raw = _header(headers, CONTENT_TYPE_HEADER)
    if not raw:
        return None, {}
    media, _, rest = raw.partition(";")
    parameters: dict[str, str] = {}
    for part in rest.split(";"):
        key, _, value = part.partition("=")
        if key.strip():
            parameters[key.strip().lower()] = value.strip().strip('"')
    return media.strip().lower(), parameters


def _is_protobuf(content_type: str | None) -> bool:
    if content_type is None:
        return False
    return content_type in _PROTOBUF_TYPES or content_type.endswith("+protobuf")


def _named_type(parameters: Mapping[str, str]) -> str | None:
    for key in _TYPE_PARAMETERS:
        if value := parameters.get(key):
            return value
    return None
