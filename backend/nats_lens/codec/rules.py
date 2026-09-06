"""Step 2: subject pattern to message type.

NATS subjects are dot-separated tokens with two wildcards: `*` stands for exactly
one token, `>` for one or more and only as the final token. Several patterns can
therefore claim the same subject, and the design's promise is that the most
specific one wins "even when it is listed lower" -- so the order cannot come from
the order rules were typed in.

Specificity is scored on four things, most significant first:

    1. how many literal tokens the pattern names
    2. how many tokens it pins before its first wildcard
    3. whether it avoids `>`
    4. how few `*` it uses

The listed order for the last two is the other way around, and it cannot be: the
required ordering `orders.new` > `orders.*` > `orders.>` has `orders.>` winning on
"fewer `*`" (it has none) while being the least specific pattern of the three. A
tail wildcard swallows an unbounded number of tokens, which outweighs any count of
single-token ones, so the tail term is scored first.

Ties fall to the rule's explicit `precedence`, then to a server-scoped rule over a
global one, then to insertion order -- deterministic at every step, because "which
rule decoded this message" is rendered in the UI and has to be reproducible.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Iterator
from typing import Final

import msgspec

TOKEN_SEPARATOR: Final = "."
STAR: Final = "*"
TAIL: Final = ">"

_MAX_TOKENS: Final = 255

# Bit layout of the packed score. Wide enough for any subject a server will
# accept, and packed rather than compared as a tuple because `SubjectRuleOut`
# exposes one integer that the UI sorts on.
_LITERAL_SHIFT: Final = 24
_PREFIX_SHIFT: Final = 16
_TAIL_SHIFT: Final = 15
_STAR_SHIFT: Final = 7

_ID_LIKE: Final = re.compile(r"^(?:\d+|[0-9a-fA-F]{8,}|[0-9a-fA-F-]{16,})$")
_INVALID_TOKEN_CHARS: Final = frozenset(" \t\r\n")


class Rule(msgspec.Struct, frozen=True):
    """A stored subject rule, as the chain needs it. No database types."""

    id: uuid.UUID
    pattern: str
    type_full_name: str
    server_id: uuid.UUID | None = None
    precedence: int = 0
    enabled: bool = True


class RuleSet:
    """Rules for one server, ordered once so every message is matched the same way.

    Construction sorts; matching is a scan of a short list. Both are cheap, and a
    RuleSet is rebuilt only when a rule changes, so the ordering cost is paid per
    edit rather than per message.
    """

    __slots__ = ("_ordered",)

    def __init__(self, rules: Iterable[Rule] = ()) -> None:
        # `index` is the insertion order, carried through the sort so that two
        # otherwise indistinguishable rules always resolve the same way.
        enabled = [(index, rule) for index, rule in enumerate(rules) if rule.enabled]
        enabled.sort(key=lambda item: _sort_key(item[1], item[0]))
        self._ordered: tuple[Rule, ...] = tuple(rule for _, rule in enabled)

    def __len__(self) -> int:
        return len(self._ordered)

    def __iter__(self) -> Iterator[Rule]:
        return iter(self._ordered)

    @property
    def ordered(self) -> tuple[Rule, ...]:
        """Every enabled rule, most specific first. The order the UI lists."""
        return self._ordered

    def match(self, subject: str) -> Rule | None:
        """The one rule that claims `subject`, or None."""
        for rule in self._ordered:
            if pattern_matches(rule.pattern, subject):
                return rule
        return None

    def all_matches(self, subject: str) -> tuple[Rule, ...]:
        """Every rule that claims `subject`, in the order they were considered."""
        return tuple(rule for rule in self._ordered if pattern_matches(rule.pattern, subject))


def _sort_key(rule: Rule, index: int) -> tuple[int, int, int, int]:
    """Most specific first, then explicit precedence, then scope, then insertion order.

    A rule pinned to one server beats an identical rule left global: the operator
    who narrowed it meant it for that server.
    """
    return (
        -specificity(rule.pattern),
        -rule.precedence,
        0 if rule.server_id is not None else 1,
        index,
    )


def specificity(pattern: str) -> int:
    """How narrowly `pattern` claims subjects. Higher wins; equal scores are ties."""
    tokens = pattern.split(TOKEN_SEPARATOR)
    literals = 0
    prefix = 0
    stars = 0
    counting_prefix = True
    has_tail = False

    for token in tokens:
        if token == TAIL:
            has_tail = True
            counting_prefix = False
        elif token == STAR:
            stars += 1
            counting_prefix = False
        else:
            literals += 1
            if counting_prefix:
                prefix += 1

    return (
        (min(literals, _MAX_TOKENS) << _LITERAL_SHIFT)
        | (min(prefix, _MAX_TOKENS) << _PREFIX_SHIFT)
        | ((0 if has_tail else 1) << _TAIL_SHIFT)
        | ((_MAX_TOKENS - min(stars, _MAX_TOKENS)) << _STAR_SHIFT)
    )


def pattern_matches(pattern: str, subject: str) -> bool:
    """NATS wildcard matching: `*` is one token, `>` is one or more trailing tokens."""
    if not pattern or not subject:
        return False

    tokens = subject.split(TOKEN_SEPARATOR)
    parts = pattern.split(TOKEN_SEPARATOR)

    for index, part in enumerate(parts):
        if part == TAIL:
            # `>` is only a wildcard as the final token, and it needs something to
            # swallow: `orders.>` matches `orders.new` but not `orders` itself.
            return index == len(parts) - 1 and len(tokens) > index
        if index >= len(tokens):
            return False
        if part != STAR and part != tokens[index]:
            return False

    return len(tokens) == len(parts)


def validate_pattern(pattern: str) -> str | None:
    """The reason `pattern` is not a usable subject pattern, or None if it is."""
    if not pattern:
        return "A subject pattern cannot be empty."

    tokens = pattern.split(TOKEN_SEPARATOR)
    for index, token in enumerate(tokens):
        if not token:
            return f"`{pattern}` has an empty token. Subject tokens are separated by a single dot."
        if _INVALID_TOKEN_CHARS & set(token):
            return f"`{pattern}` contains whitespace. NATS subjects cannot."
        if TAIL in token and token != TAIL:
            return f"`{token}` mixes `>` with other characters. `>` must be a token of its own."
        if STAR in token and token != STAR:
            return f"`{token}` mixes `*` with other characters. `*` must be a token of its own."
        if token == TAIL and index != len(tokens) - 1:
            return f"`{pattern}` uses `>` before the end. `>` is only a wildcard as the last token."

    return None


def suggested_pattern(subject: str) -> str:
    """The pattern the UI offers for an unmapped subject.

    `telemetry.device.4471.temp` becomes `telemetry.device.*.temp`, because the
    device id is the part that varies and the rest is the shape of the topic. Only
    tokens that read as identifiers are generalised; a word stays a word.
    """
    tokens = subject.split(TOKEN_SEPARATOR)
    return TOKEN_SEPARATOR.join(
        STAR if _looks_like_an_identifier(token) else token for token in tokens
    )


def _looks_like_an_identifier(token: str) -> bool:
    if token in (STAR, TAIL) or not token:
        return False
    if _ID_LIKE.match(token):
        return True
    # `ord_8812`, `device-4471`: long enough to be generated, and carrying digits.
    return len(token) >= 6 and any(ch.isdigit() for ch in token)
