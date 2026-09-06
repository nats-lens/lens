"""The provenance envelope, and the rule that a missing number is never a zero."""

from __future__ import annotations

import msgspec
import pytest

from nats_lens.provenance import Reason, Source, Sourced, Unavailable

pytestmark = pytest.mark.unit


def test_known_value_carries_its_source() -> None:
    s = Sourced.known(128, Source.MONITOR)
    assert s.value == 128
    assert s.source is Source.MONITOR
    assert s.is_known
    assert s.unavailable is None


def test_missing_value_is_none_and_names_the_fix() -> None:
    s: Sourced[int] = Sourced.missing(Source.MONITOR, Reason.MONITORING_NOT_CONFIGURED)
    assert s.value is None, "a value we could not see must never be rendered as 0"
    assert not s.is_known
    assert s.unavailable is not None
    assert s.unavailable.reason is Reason.MONITORING_NOT_CONFIGURED
    assert "http_port" in s.unavailable.fix
    assert s.unavailable.doc is not None


@pytest.mark.parametrize("reason", list(Reason))
def test_every_reason_has_a_fix(reason: Reason) -> None:
    """A reason with no remedy is a dead end for the user. There are none."""
    u = Unavailable.of(reason)
    assert u.fix, f"{reason} has no fix sentence"
    assert len(u.fix) > 30, f"{reason}'s fix is too vague to act on: {u.fix!r}"


def test_detail_is_appended_to_the_fix() -> None:
    u = Unavailable.of(Reason.MONITORING_UNREACHABLE, "Connection refused on port 8222.")
    assert u.fix.endswith("Connection refused on port 8222.")


def test_round_trips_through_json() -> None:
    """Sourced is generic; Litestar serialises it, so it has to survive the trip."""
    s = Sourced.known(41.2, Source.CLIENT)
    back = msgspec.json.decode(msgspec.json.encode(s), type=Sourced[float])
    assert back.value == pytest.approx(41.2)
    assert back.source is Source.CLIENT
