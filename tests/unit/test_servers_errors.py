"""nats-py exceptions on their way to the screen.

The design prints `nats.errors.NoServersError` on the offline card, verbatim. That
string is the contract: it survives from the exception, through the mapping, into
both `ProblemDetail.nats_error` and the `last_error` field of an ordinary 200.
"""

from __future__ import annotations

import pytest
from nats import errors as nats_errors
from nats.js import errors as js_errors

from nats_lens.conn.errors import NatsProblem, classify, describe, nats_error_name, problem
from nats_lens.crypto import SecretError

pytestmark = pytest.mark.unit


def test_the_exception_class_survives_with_its_module() -> None:
    """`NoServersError` alone is not searchable. The dotted path is."""
    assert nats_error_name(nats_errors.NoServersError()) == "nats.errors.NoServersError"
    assert nats_error_name(js_errors.NotFoundError()) == "nats.js.errors.NotFoundError"


def test_a_non_nats_exception_claims_no_nats_error() -> None:
    assert nats_error_name(ConnectionRefusedError()) is None
    assert problem(ConnectionRefusedError()).nats_error is None


def test_describe_is_the_string_the_offline_card_shows() -> None:
    text = describe(nats_errors.NoServersError())
    assert text.startswith("nats.errors.NoServersError: ")
    # nats-py's own wording, not a paraphrase of ours.
    assert text.endswith(str(nats_errors.NoServersError()))


def test_describe_falls_back_to_the_bare_class_name() -> None:
    assert describe(ValueError("bad")) == "ValueError: bad"
    assert describe(ValueError()) == "ValueError"


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (nats_errors.NoServersError(), 503),
        (nats_errors.AuthorizationError(), 502),
        (nats_errors.InvalidUserCredentialsError(), 502),
        (nats_errors.SecureConnRequiredError(), 502),
        (nats_errors.ConnectionClosedError(), 503),
        (nats_errors.ConnectionDrainingError(), 409),
        (nats_errors.TimeoutError(), 504),
        (nats_errors.MaxPayloadError(), 413),
        (nats_errors.BadSubjectError(), 400),
        (js_errors.NotFoundError(), 404),
        (js_errors.BadRequestError(), 400),
        (js_errors.ServiceUnavailableError(), 503),
        (SecretError("key gone"), 500),
        (ConnectionRefusedError(), 503),
        (ValueError("unexpected"), 500),
    ],
)
def test_every_kind_of_failure_gets_a_status_worth_showing(exc: Exception, status: int) -> None:
    assert classify(exc)[0] == status
    assert problem(exc).status == status


def test_a_subclass_is_matched_before_its_base() -> None:
    """The table is ordered, and the order is load-bearing.

    `js.errors.APIError` extends `nats.errors.Error`, and `FetchTimeoutError` extends
    `nats.errors.TimeoutError`. Reaching the generic entry first would turn every
    JetStream 404 into a 502.
    """
    assert classify(js_errors.KeyNotFoundError())[0] == 404
    assert classify(js_errors.FetchTimeoutError())[0] == 504
    assert classify(nats_errors.Error())[0] == 502


def test_a_problem_detail_is_complete() -> None:
    detail = problem(nats_errors.NoServersError(), instance="/api/servers/x/connect")
    assert detail.status == 503
    assert detail.title == "No NATS server answered"
    assert detail.nats_error == "nats.errors.NoServersError"
    assert detail.instance == "/api/servers/x/connect"
    assert detail.type == "/problems/noservers"


def test_the_response_body_carries_the_nats_error() -> None:
    """`app.py` registers routers and nothing else, so the exception class reaches
    the frontend through Litestar's `extra` rather than a custom handler."""
    exc = NatsProblem.of(nats_errors.NoServersError())
    assert exc.status_code == 503
    assert isinstance(exc.extra, dict)
    assert exc.extra["nats_error"] == "nats.errors.NoServersError"
    assert "nats.errors.NoServersError" in exc.detail
