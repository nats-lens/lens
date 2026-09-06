"""Where a number came from.

The single most important contract in nats-lens. A plain NATS client cannot see
server-wide counters -- connections, subscriptions, slow consumers, throughput,
routes are not in the client protocol. They need the HTTP monitoring port or a
$SYS account connection.

So the API never returns a bare number. It returns a value *and* its source, or
no value *and* the reason plus the fix. The UI renders a badge from the first and
an empty state from the second; it is never left to guess, and it never shows a
zero for something it simply could not see.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Generic, TypeVar

import msgspec

T = TypeVar("T")


class Source(StrEnum):
    """How a value was obtained. Rendered verbatim as the UI's source badge."""

    CLIENT = "client"
    """The NATS client connection itself: server INFO, RTT, max_payload, our own counters."""

    JETSTREAM = "jetstream"
    """The JetStream API over the client connection: streams, consumers, KV, object store."""

    MONITOR = "monitor"
    """The HTTP monitoring port: /varz, /connz, /routez, /jsz, /healthz."""

    SYSTEM = "system"
    """The $SYS account: STATSZ heartbeats, CONNECT/DISCONNECT events, advisories."""

    SAMPLED = "sampled"
    """Observed by nats-lens while it was watching. Not a server-side total."""


class Reason(StrEnum):
    """Why a value is missing. Each maps to a fixed, actionable sentence."""

    MONITORING_NOT_CONFIGURED = "monitoring_not_configured"
    MONITORING_UNREACHABLE = "monitoring_unreachable"
    SYSTEM_ACCOUNT_NOT_CONFIGURED = "system_account_not_configured"
    SYSTEM_ACCOUNT_DENIED = "system_account_denied"
    NOT_CONNECTED = "not_connected"
    JETSTREAM_NOT_ENABLED = "jetstream_not_enabled"
    JETSTREAM_NOT_ENABLED_FOR_ACCOUNT = "jetstream_not_enabled_for_account"
    NOT_SUPPORTED_BY_SERVER = "not_supported_by_server"


_FIXES: dict[Reason, tuple[str, str | None]] = {
    Reason.MONITORING_NOT_CONFIGURED: (
        "This server has no monitoring URL. Start nats-server with `-m 8222` (or set "
        "`http_port: 8222` in its config) and add the URL under the server's settings.",
        "https://docs.nats.io/running-a-nats-service/nats_admin/monitoring",
    ),
    Reason.MONITORING_UNREACHABLE: (
        "The monitoring URL is set but did not answer. Check that the port is open to "
        "nats-lens and that the server was started with monitoring enabled.",
        "https://docs.nats.io/running-a-nats-service/nats_admin/monitoring",
    ),
    Reason.SYSTEM_ACCOUNT_NOT_CONFIGURED: (
        "No system account is configured. Add $SYS credentials to this server to receive "
        "connect and disconnect events and STATSZ heartbeats as they happen.",
        "https://docs.nats.io/running-a-nats-service/configuration/sys_accounts",
    ),
    Reason.SYSTEM_ACCOUNT_DENIED: (
        "The system account credentials were rejected. The user must be a member of $SYS.",
        "https://docs.nats.io/running-a-nats-service/configuration/sys_accounts",
    ),
    Reason.NOT_CONNECTED: (
        "nats-lens is not connected to this server, so nothing can be read from it.",
        None,
    ),
    Reason.JETSTREAM_NOT_ENABLED: (
        "JetStream is not enabled on this server. Start nats-server with `-js`, or enable "
        "`jetstream` in its config.",
        "https://docs.nats.io/nats-concepts/jetstream",
    ),
    Reason.JETSTREAM_NOT_ENABLED_FOR_ACCOUNT: (
        "The server runs JetStream, but not for the account nats-lens connected as. Connect "
        "with a user in an account that has it, or add `jetstream: enabled` to this account. "
        "The system account $SYS does not have JetStream unless it is given it explicitly.",
        "https://docs.nats.io/running-a-nats-service/configuration/resource_management",
    ),
    Reason.NOT_SUPPORTED_BY_SERVER: (
        "This server version does not implement the API this view needs.",
        None,
    ),
}


class Unavailable(msgspec.Struct, frozen=True):
    """A missing value, with the fix named."""

    reason: Reason
    fix: str
    doc: str | None = None

    @classmethod
    def of(cls, reason: Reason, detail: str | None = None) -> Unavailable:
        fix, doc = _FIXES[reason]
        return cls(reason=reason, fix=f"{fix} {detail}" if detail else fix, doc=doc)


# UP046 suggests PEP 695 `class Sourced[T]`. Do not: this module uses postponed
# annotations, and a PEP 695 type parameter is scoped to the class rather than the
# module, so msgspec's forward-reference resolution cannot find `T` and OpenAPI
# generation fails with `NameError: name 'T' is not defined`.
class Sourced(msgspec.Struct, Generic[T], frozen=True):  # noqa: UP046
    """A value that knows where it came from, or why it is absent.

    Exactly one of `value` and `unavailable` is meaningful. When `unavailable` is
    set the UI shows the empty state and `value` is None -- never a zero standing
    in for something we could not see.
    """

    value: T | None
    source: Source
    at: datetime
    unavailable: Unavailable | None = None

    @classmethod
    def known(cls, value: T, source: Source) -> Sourced[T]:
        return cls(value=value, source=source, at=datetime.now(UTC))

    @classmethod
    def missing(cls, source: Source, reason: Reason, detail: str | None = None) -> Sourced[T]:
        return cls(
            value=None,
            source=source,
            at=datetime.now(UTC),
            unavailable=Unavailable.of(reason, detail),
        )

    @property
    def is_known(self) -> bool:
        return self.unavailable is None
