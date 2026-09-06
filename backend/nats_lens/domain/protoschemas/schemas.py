"""The protobuf descriptor registry and the subject rules that select types.

FROZEN CONTRACT -- see domain/common.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import msgspec

from nats_lens.codec.schemas import Decoded


class TypeSummary(msgspec.Struct, frozen=True):
    full_name: str
    field_names: tuple[str, ...]
    field_count: int
    rule_count: int
    last_seen: datetime | None = None
    """`sampled` -- when nats-lens last decoded a message as this type."""


class Origin(StrEnum):
    """How a definition reached nats-lens."""

    UPLOAD = "upload"
    """Sent through the UI. nats-lens owns the file and may delete it."""
    MOUNTED = "mounted"
    """Found in the mounted directory. Read-only: the tree is the source of truth."""


class ScanStatus(StrEnum):
    REGISTERED = "registered"
    UNCHANGED = "unchanged"
    REMOVED = "removed"
    FAILED = "failed"


class ScanEntry(msgspec.Struct, frozen=True):
    """What happened to one file. A failure names why, in protoc's own words."""

    path: str
    origin: Origin
    package: str | None
    status: ScanStatus
    detail: str | None


class ScanReport(msgspec.Struct, frozen=True):
    upload_dir: str
    mount_dir: str | None
    mount_dir_present: bool
    """False when a directory is configured but not actually mounted -- the most
    common reason a scan finds nothing, and worth saying rather than showing an
    empty list."""
    entries: tuple[ScanEntry, ...]


class DescriptorSummary(msgspec.Struct, frozen=True):
    id: uuid.UUID
    package: str
    source_filename: str
    protoc_version: str | None
    size_bytes: int
    imported_only: bool
    type_count: int
    rule_count: int
    registered_at: datetime
    origin: Origin = Origin.UPLOAD
    source_path: str | None = None
    note: str | None = None


class DescriptorDetail(msgspec.Struct, frozen=True):
    id: uuid.UUID
    package: str
    source_filename: str
    protoc_version: str | None
    size_bytes: int
    imported_only: bool
    registered_at: datetime
    description: str
    types: tuple[TypeSummary, ...]
    origin: Origin = Origin.UPLOAD
    source_path: str | None = None


class TypeChoice(msgspec.Struct, frozen=True):
    """One message type, flat, for the subject-rule picker.

    Flat rather than nested under its descriptor because that is how it is
    chosen: an operator knows the type name, not which file it arrived in.
    """

    full_name: str
    package: str
    field_count: int
    field_names: tuple[str, ...]
    origin: Origin
    source_filename: str
    rule_count: int


class DescriptorUpload(msgspec.Struct, frozen=True):
    """Either a `.proto` source (compiled here with protoc) or a FileDescriptorSet."""

    filename: str
    content_b64: str
    is_descriptor_set: bool = False
    imported_only: bool = False
    note: str | None = None


class SubjectRuleIn(msgspec.Struct, frozen=True):
    pattern: str
    type_full_name: str
    server_id: uuid.UUID | None = None
    precedence: int = 0
    enabled: bool = True


class SubjectRuleOut(msgspec.Struct, frozen=True):
    id: uuid.UUID
    pattern: str
    type_full_name: str
    server_id: uuid.UUID | None
    precedence: int
    specificity: int
    """Computed. Higher wins, which is how `orders.new` beats `orders.*` beats `orders.>`."""
    enabled: bool
    hits: int
    """`sampled` -- matches observed since this process started."""


class UnmappedSubject(msgspec.Struct, frozen=True):
    """A subject seen on the wire that no rule claims. `sampled`."""

    subject: str
    hits: int
    first_seen: datetime
    last_seen: datetime
    suggested_pattern: str


class ResolutionStep(msgspec.Struct, frozen=True):
    n: int
    name: str
    description: str


class DecodePreviewResult(msgspec.Struct, frozen=True):
    decoded: Decoded
    matched_rule: SubjectRuleOut | None = None
