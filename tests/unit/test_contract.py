"""Structural guards on the frozen API contract.

The product's whole claim is that it never shows a number it could not actually
see. That is a promise about data, so it is enforced against the data -- not left
to whoever writes the next endpoint to remember.
"""

from __future__ import annotations

import typing
from typing import Any, get_args, get_origin

import msgspec
import pytest

from nats_lens.app import create_app
from nats_lens.provenance import Sourced

pytestmark = pytest.mark.unit


# Counters that a plain NATS client genuinely cannot see. Each needs the HTTP
# monitoring port or a $SYS connection, so each must arrive with its provenance
# or as an explicit null -- never as a bare number the UI would render as 0.
SERVER_WIDE_COUNTERS = frozenset(
    {
        "connections",
        "total_connections",
        "subscriptions",
        "slow_consumers",
        "routes",
        "remotes",
        "leafnodes",
        "in_msgs",
        "out_msgs",
        "in_bytes",
        "out_bytes",
    }
)


def _is_struct(t: Any) -> bool:
    return isinstance(t, type) and issubclass(t, msgspec.Struct)


# Responses that are monitor-sourced in their entirety: the endpoint reads the
# HTTP monitoring port and nothing else, and raises a problem detail when it is
# unavailable rather than returning a body full of zeros. Their rows therefore
# need no per-field wrapper -- the whole payload is the unit of provenance.
WHOLE_RESPONSE_MONITOR_SOURCED = frozenset({"ConnzPage", "RoutezSummary"})


def _is_numeric(t: Any) -> bool:
    """Only a number can be mistaken for a count. A tuple of rows cannot."""
    if t in (int, float):
        return True
    origin = get_origin(t)
    if origin is not None and origin in (
        typing.Union,
        getattr(__import__("types"), "UnionType", None),
    ):
        return any(a in (int, float) for a in get_args(t))
    return False


def _walk(t: Any, guarded: bool, seen: set[Any], out: list[tuple[str, str, bool]]) -> None:
    """Collect (struct, field, guarded) for every server-wide counter reachable from `t`.

    `guarded` becomes True once the path has passed through a `Sourced[...]`
    wrapper or an optional field, either of which forces the frontend to handle
    absence explicitly.
    """
    origin = get_origin(t)
    if origin is not None:
        if origin is Sourced or (isinstance(origin, type) and issubclass(origin, Sourced)):
            guarded = True
        args = get_args(t)
        is_union = origin in (typing.Union, getattr(__import__("types"), "UnionType", None))
        if is_union and type(None) in args:
            guarded = True
        for a in args:
            if a is not type(None):
                _walk(a, guarded, seen, out)
        return

    if _is_struct(t):
        if (t, guarded) in seen:
            return
        seen.add((t, guarded))
        if t.__name__ in WHOLE_RESPONSE_MONITOR_SOURCED:
            guarded = True
        hints = typing.get_type_hints(t, include_extras=False)
        for field in t.__struct_fields__:
            ann = hints.get(field, Any)
            if field in SERVER_WIDE_COUNTERS and _is_numeric(ann):
                out.append((t.__name__, field, guarded))
            _walk(ann, guarded, seen, out)


def _response_types() -> list[Any]:
    """Every handler's declared return type.

    Read from Litestar's own parsed signature rather than `get_type_hints`, which
    cannot resolve the handlers' postponed annotations from outside their module.
    """
    app = create_app()
    types_: list[Any] = []
    for route in app.routes:
        for handler in getattr(route, "route_handlers", []):
            parsed = getattr(handler, "parsed_fn_signature", None)
            if parsed is None:
                continue
            annotation = parsed.return_type.annotation
            if annotation not in (None, type(None)):
                types_.append(annotation)
    return types_


def test_every_route_declares_a_return_type() -> None:
    assert _response_types(), "no route return types found -- the contract is not wired"


def test_server_wide_counters_are_never_reachable_as_bare_numbers() -> None:
    """The rule that keeps `0 connections` from ever shipping.

    Any counter a client cannot see must sit behind `Sourced[...]` (which carries
    the reason and the fix) or behind an optional (which forces the UI to handle
    the null). A bare `int` on a always-present path would let the frontend render
    a zero for something the backend never observed.
    """
    findings: list[tuple[str, str, bool]] = []
    seen: set[Any] = set()
    for t in _response_types():
        _walk(t, False, seen, findings)

    unguarded = sorted({(s, f) for s, f, g in findings if not g})
    assert not unguarded, (
        "these server-wide counters are reachable without provenance or a null:\n  "
        + "\n  ".join(f"{s}.{f}" for s, f in unguarded)
        + "\nWrap the field (or its container) in Sourced[...], or make it optional."
    )


def test_no_secret_material_is_reachable_from_any_response() -> None:
    """A decrypted credential in a response is the worst bug this tool could ship."""
    banned = {"ciphertext", "nonce", "password", "token", "seed", "nkey_seed", "private_key"}
    found: list[str] = []
    seen: set[Any] = set()

    def walk_all(t: Any) -> None:
        origin = get_origin(t)
        if origin is not None:
            for a in get_args(t):
                if a is not type(None):
                    walk_all(a)
            return
        if _is_struct(t):
            if t in seen:
                return
            seen.add(t)
            hints = typing.get_type_hints(t, include_extras=False)
            for field in t.__struct_fields__:
                if field in banned:
                    found.append(f"{t.__name__}.{field}")
                walk_all(hints.get(field, Any))

    for t in _response_types():
        walk_all(t)
    assert not found, f"secret-bearing fields reachable from an API response: {found}"


def test_monitor_only_responses_really_are_monitor_only() -> None:
    """The exemption above is load-bearing, so it is checked rather than trusted.

    Every exempted struct must live in the monitor schemas module -- if one is ever
    moved or reused on a mixed-source endpoint, this fails and the exemption has to
    be re-argued.
    """
    from nats_lens.domain.monitor import schemas as monitor_schemas

    for name in WHOLE_RESPONSE_MONITOR_SOURCED:
        struct = getattr(monitor_schemas, name, None)
        assert struct is not None, f"{name} is exempted but no longer exists"
        assert struct.__module__ == monitor_schemas.__name__, (
            f"{name} is exempted as monitor-only but now lives in {struct.__module__}"
        )


def test_mixed_source_responses_do_wrap_their_counters() -> None:
    """The guard has teeth: the Servers screen's traffic figures are wrapped."""
    from nats_lens.domain.servers.schemas import ServerDetail, ServerSummary

    for struct in (ServerSummary, ServerDetail):
        hints = typing.get_type_hints(struct, include_extras=False)
        assert get_origin(hints["traffic"]) is Sourced, (
            f"{struct.__name__}.traffic must be Sourced -- it is the one field on the "
            "Servers screen a plain client cannot see"
        )


def test_openapi_schema_generates_completely() -> None:
    spec = create_app().openapi_schema.to_schema()
    assert len(spec["paths"]) >= 40
    assert spec["components"]["schemas"]
    for path, ops in spec["paths"].items():
        for verb, op in ops.items():
            assert op.get("responses"), f"{verb.upper()} {path} declares no response"
