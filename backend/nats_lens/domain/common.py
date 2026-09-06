"""Shapes shared across domains.

FROZEN CONTRACT. Wave-1+ agents may add fields with defaults; they may not
rename, retype or remove anything here without reporting first.
"""

from __future__ import annotations

from enum import StrEnum

import msgspec


class KeyValueRow(msgspec.Struct, frozen=True):
    """A label/value pair as the design's detail cards render them."""

    k: str
    v: str


class ProblemDetail(msgspec.Struct, frozen=True):
    """RFC 9457. What every 4xx/5xx carries."""

    type: str
    title: str
    status: int
    detail: str
    instance: str | None = None
    nats_error: str | None = None
    """The nats-py exception class name, e.g. `NoServersError`, shown verbatim in the UI."""


class HealthStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


class AppHealth(msgspec.Struct, frozen=True):
    status: HealthStatus
    version: str
    database: bool
    servers_registered: int
    servers_connected: int
