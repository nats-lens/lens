"""nats-py exceptions, turned into something a person can act on.

The design puts `nats.errors.NoServersError` on the offline card verbatim, and
that is deliberate: the dotted class path is the part someone can search for or
paste into an issue, so it is the payload rather than decoration. Everything here
preserves it -- as `ProblemDetail.nats_error` on the error path, and as
`describe()` on the `last_error` field of an otherwise ordinary 200.
"""

from __future__ import annotations

from typing import Any

from litestar.exceptions import HTTPException
from nats import errors as nats_errors
from nats.js import errors as js_errors

from nats_lens.crypto import SecretError
from nats_lens.domain.common import ProblemDetail

# First match wins, so subclasses come before the bases they extend. The bases at
# the end are the safety net: an exception nats-py adds tomorrow still gets a
# sensible status rather than a 500.
_TABLE: tuple[tuple[type[BaseException], int, str], ...] = (
    (SecretError, 500, "A stored credential could not be opened"),
    (nats_errors.NoServersError, 503, "No NATS server answered"),
    (nats_errors.AuthorizationError, 502, "The NATS server rejected our credentials"),
    (nats_errors.InvalidUserCredentialsError, 502, "The credentials could not be read"),
    (nats_errors.SecureConnRequiredError, 502, "The server requires TLS"),
    (nats_errors.SecureConnWantedError, 502, "TLS was asked for but the server does not offer it"),
    (nats_errors.SecureConnFailedError, 502, "The TLS handshake failed"),
    (nats_errors.ConnectionDrainingError, 409, "The connection is draining"),
    (nats_errors.ConnectionReconnectingError, 503, "The connection is reconnecting"),
    (nats_errors.ConnectionClosedError, 503, "The connection is closed"),
    (nats_errors.StaleConnectionError, 503, "The connection went stale"),
    (nats_errors.NoRespondersError, 404, "Nothing is listening on that subject"),
    (nats_errors.MaxPayloadError, 413, "The payload is larger than the server allows"),
    (nats_errors.BadSubjectError, 400, "That is not a valid subject"),
    (nats_errors.BadSubscriptionError, 400, "That subscription is not usable"),
    (js_errors.BucketNotFoundError, 404, "No such bucket"),
    (js_errors.KeyNotFoundError, 404, "No such key"),
    (js_errors.ObjectNotFoundError, 404, "No such object"),
    (js_errors.NotFoundError, 404, "JetStream has no such object"),
    (js_errors.BadRequestError, 400, "JetStream rejected the request"),
    (js_errors.ServiceUnavailableError, 503, "JetStream is not available"),
    (js_errors.APIError, 502, "The JetStream API returned an error"),
    (nats_errors.TimeoutError, 504, "The NATS server did not answer in time"),
    (nats_errors.Error, 502, "The NATS client reported an error"),
    (TimeoutError, 504, "The operation timed out"),
    (OSError, 503, "The NATS server could not be reached"),
)

_FALLBACK = (500, "Something went wrong talking to NATS")


def nats_error_name(exc: BaseException) -> str | None:
    """The dotted class path, when the exception came from nats-py.

    `nats.errors.NoServersError`, not `NoServersError`: the module is half of what
    makes the string searchable, and the design shows it in full.
    """
    cls = type(exc)
    module = cls.__module__
    if module == "nats" or module.startswith("nats."):
        return f"{module}.{cls.__name__}"
    return None


def describe(exc: BaseException) -> str:
    """The one-line form shown as `last_error` on the Servers screen.

    `nats.errors.NoServersError: nats: no servers available for connection`. The
    message half is nats-py's own wording, never ours -- if it changes upstream,
    the UI should show the change rather than a stale paraphrase.
    """
    name = nats_error_name(exc) or type(exc).__name__
    text = str(exc).strip()

    # Several nats-py errors hardcode `__str__` and ignore the message they were
    # constructed with, so anything we added -- which URLs, which timeout -- would
    # vanish. Keep their wording, then append ours when it is genuinely different.
    detail = exc.args[0] if exc.args and isinstance(exc.args[0], str) else ""
    detail = detail.strip()
    if detail and detail not in text:
        text = f"{text} ({detail})" if text else detail

    return f"{name}: {text}" if text else name


def classify(exc: BaseException) -> tuple[int, str]:
    for kind, status, title in _TABLE:
        if isinstance(exc, kind):
            return status, title
    return _FALLBACK


def _slug(status: int, exc: BaseException) -> str:
    name = nats_error_name(exc)
    if name is not None:
        return name.rsplit(".", 1)[-1].removesuffix("Error").lower() or "nats"
    return f"http-{status}"


def problem(exc: BaseException, *, instance: str | None = None) -> ProblemDetail:
    """RFC 9457 for a NATS failure, with the exception class kept intact."""
    status, title = classify(exc)
    return ProblemDetail(
        type=f"/problems/{_slug(status, exc)}",
        title=title,
        status=status,
        detail=describe(exc),
        instance=instance,
        nats_error=nats_error_name(exc),
    )


class NatsProblem(HTTPException):
    """A `ProblemDetail` on the wire.

    Litestar's default error response carries `extra` verbatim, which is how
    `nats_error` reaches the frontend without an application-wide exception
    handler -- `app.py` registers the routers and nothing else, by ownership.
    """

    def __init__(self, detail: ProblemDetail) -> None:
        self.problem = detail
        extra: dict[str, Any] = {
            "type": detail.type,
            "title": detail.title,
            "nats_error": detail.nats_error,
        }
        if detail.instance is not None:
            extra["instance"] = detail.instance
        super().__init__(status_code=detail.status, detail=detail.detail, extra=extra)

    @classmethod
    def of(cls, exc: BaseException, *, instance: str | None = None) -> NatsProblem:
        return cls(problem(exc, instance=instance))
