"""Object store. FROZEN CONTRACT -- see domain/common.py."""


# instance rather than sharing them, so `= {}` here is safe. Verified.

from __future__ import annotations

from datetime import datetime

import msgspec

from nats_lens.domain.jetstream.schemas import Storage


class ObjectBucketSummary(msgspec.Struct, frozen=True):
    name: str
    stream_name: str
    objects: int
    bytes: int
    storage: Storage
    replicas: int
    sealed: bool
    max_chunk_size: int
    ttl_seconds: float | None = None
    usage: float | None = None
    """Fraction of the bucket's byte limit in use, 0 to 1. None when unlimited."""
    description: str | None = None
    compressed: bool = False


class ObjectInfo(msgspec.Struct, frozen=True):
    name: str
    bucket: str
    size: int
    chunks: int
    digest: str
    """`SHA-256=...`, exactly as the server reports it."""
    modified: datetime
    deleted: bool = False
    description: str | None = None
    headers: dict[str, str] = {}
    content_type: str | None = None
    nuid: str | None = None


class ObjectBucketCreate(msgspec.Struct, frozen=True):
    name: str
    storage: Storage = Storage.FILE
    replicas: int = 1
    max_bytes: int = -1
    ttl_seconds: float | None = None
    description: str | None = None


class ObjectMetaUpdate(msgspec.Struct, frozen=True):
    """Rename an object, or change what is recorded about it.

    The bytes are untouched: this rewrites only the metadata entry, so it is
    cheap even for a multi-gigabyte object.
    """

    name: str | None = None
    description: str | None = None
    headers: dict[str, str] | None = None


class ObjectLink(msgspec.Struct, frozen=True):
    """A link object: points at another object, or at a whole bucket."""

    name: str
    target_bucket: str
    target_object: str | None = None
