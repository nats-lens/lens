"""Key-Value buckets. FROZEN CONTRACT -- see domain/common.py."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

import msgspec

from nats_lens.codec.schemas import Decoded
from nats_lens.domain.jetstream.schemas import Storage


class KvOperation(StrEnum):
    PUT = "PUT"
    DEL = "DEL"
    PURGE = "PURGE"


class BucketSummary(msgspec.Struct, frozen=True):
    name: str
    stream_name: str
    values: int
    bytes: int
    history: int
    ttl_seconds: float | None
    max_value_size: int
    storage: Storage
    replicas: int
    usage: float | None
    """Fraction of the bucket's byte limit in use, 0 to 1. None when unlimited."""
    description: str | None = None
    compressed: bool = False


class KvEntry(msgspec.Struct, frozen=True):
    key: str
    revision: int
    created: datetime
    operation: KvOperation
    size: int
    payload_b64: str | None
    decoded: Decoded | None
    delta: int = 0


class KvKeyRow(msgspec.Struct, frozen=True):
    """A row in the key table -- no value, so listing a large bucket stays cheap."""

    key: str
    revision: int
    size: int
    created: datetime
    operation: KvOperation


class KvKeyPage(msgspec.Struct, frozen=True):
    keys: tuple[KvKeyRow, ...]
    total: int
    truncated: bool
    note: str
    """`12,404 keys -- listing walks the bucket, so filter first.`"""


class KvPut(msgspec.Struct, frozen=True):
    """A write. `last_revision` makes it a compare-and-set, as the design specifies."""

    value_b64: str
    last_revision: int | None = None


class BucketCreate(msgspec.Struct, frozen=True):
    name: str
    history: int = 1
    ttl_seconds: float | None = None
    max_value_size: int = -1
    max_bytes: int = -1
    storage: Storage = Storage.FILE
    replicas: int = 1
    description: str | None = None
