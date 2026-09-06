"""How far behind a consumer is.

`num_pending` counts only messages matching the consumer's *filter*. Dividing it
by the whole stream understates the lag by exactly the ratio the filter excludes,
and the failure is the dangerous direction: on a 48k stream, a consumer on
`orders.new` that had read none of its 1,734 messages reported 3.6% -- which
reads as almost caught up when it has not started.

A fraction, not a percentage. `percent()` and `Meter` both take fractions, and a
field called `_pct` holding a percentage is what put a 10000% lag on screen.
"""

from __future__ import annotations

import msgspec
import pytest

from nats_lens.domain.jetstream.service import _lag, _matching_messages

pytestmark = pytest.mark.unit

# The real shape that exposed it: one stream, three subjects, filtered consumers.
ORDERS = {"orders.lookup": 23339, "orders.create": 23338, "orders.new": 1734}
TOTAL = sum(ORDERS.values())


def test_a_filtered_consumer_is_measured_against_its_own_subjects() -> None:
    """The regression. 1734 of 1734 undelivered is all of them, not 3.6%."""
    matching = _matching_messages(("orders.new",), ORDERS)
    assert matching == 1734
    assert _lag(1734, matching) == 1.0
    assert _lag(1608, matching) == 0.9273


def test_a_wildcard_filter_sums_the_subjects_it_matches() -> None:
    assert _matching_messages(("orders.*",), ORDERS) == TOTAL
    assert _matching_messages(("orders.>",), ORDERS) == TOTAL
    assert _matching_messages(("orders.new", "orders.create"), ORDERS) == 1734 + 23338


def test_an_unfiltered_consumer_is_measured_against_the_whole_stream() -> None:
    assert _matching_messages((), ORDERS) == TOTAL
    assert _lag(TOTAL, TOTAL) == 1.0
    assert _lag(0, TOTAL) == 0.0


def test_no_percentage_when_the_server_did_not_report_subjects() -> None:
    """NATS caps the per-subject list. A guess would be a confident wrong number."""
    assert _matching_messages(("orders.new",), None) is None
    assert _lag(10, None) is None


def test_a_filter_that_selects_nothing_has_no_percentage() -> None:
    """There is no percentage of nothing, and 0% would read as caught up."""
    assert _matching_messages(("payments.>",), ORDERS) == 0
    assert _lag(0, 0) is None


def test_the_result_stays_inside_nought_to_a_hundred() -> None:
    """`num_pending` can briefly exceed the count while the stream is written to."""
    assert _lag(9_999, 1_000) == 1.0
    assert _lag(-5, 1_000) == 0.0


def test_stream_usage_is_a_fraction_of_whichever_limit_fills_first() -> None:
    """Same convention as lag, and for the same reason: `Meter` takes 0 to 1.

    Three of four call sites had this wrong in one direction or the other while
    the field was called `usage_pct` -- two fed a percentage straight into a
    meter, one divided by 100 to undo it.
    """
    from nats_lens.domain.jetstream.schemas import Discard, StreamLimits, StreamState
    from nats_lens.domain.jetstream.service import _usage

    state = StreamState(
        messages=500, bytes=250, first_seq=1, last_seq=500, consumer_count=0, num_deleted=0
    )
    half_by_bytes = StreamLimits(
        max_consumers=-1,
        max_msgs=0,
        max_bytes=500,
        max_age_seconds=0,
        max_msg_size=-1,
        max_msgs_per_subject=0,
        duplicate_window_seconds=0,
        discard=Discard.OLD,
    )
    assert _usage(state, half_by_bytes) == 0.5

    # Whichever limit fills first wins: 500/1000 by bytes, 500/625 by count.
    tighter_by_count = msgspec.structs.replace(half_by_bytes, max_bytes=1000, max_msgs=625)
    assert _usage(state, tighter_by_count) == 0.8

    # No limit is None, never 0 -- 0 would draw an empty meter for an unbounded
    # stream, which reads as "plenty of room" rather than "no limit set".
    unbounded = msgspec.structs.replace(half_by_bytes, max_bytes=0)
    assert _usage(state, unbounded) is None
