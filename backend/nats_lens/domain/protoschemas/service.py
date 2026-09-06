"""The Schemas screen: descriptors, subject rules, and what the chain observed.

Three kinds of state meet here, and keeping them apart is the point of the file.

The registry -- descriptors, message types, subject rules -- is in SQLite, and
is the only part that survives a restart.

The compiled view of it -- a `DescriptorPool` and a sorted `RuleSet` -- is derived,
and rebuilt rather than mutated. A descriptor pool cannot forget a file, so
re-uploading a changed `.proto` into a live pool would need a restart to take
effect; throwing the pool away costs a few milliseconds per registry edit and
nothing at all per message.

What the chain saw -- rule hits, when a type last decoded, which subjects nobody
has mapped -- is counted in this process and nowhere else. Those are `sampled`
figures in the provenance model: true about the window nats-lens was watching,
and not server-side totals. Writing them to the registry would turn an honest
sample into a number that looks authoritative and is not, so they stay in memory
and reset with the process.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import threading
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import anyio.to_thread
import msgspec

from nats_lens.codec import chain
from nats_lens.codec import protobuf as proto
from nats_lens.codec.rules import Rule, RuleSet, specificity, suggested_pattern, validate_pattern
from nats_lens.codec.schemas import Decoded, DecodePreview, ResolvedBy
from nats_lens.db.models import Descriptor, MessageType, SubjectRule
from nats_lens.domain.protoschemas.repository import SchemaRepository
from nats_lens.domain.protoschemas.schemas import (
    DecodePreviewResult,
    DescriptorDetail,
    DescriptorSummary,
    DescriptorUpload,
    Origin,
    ResolutionStep,
    ScanEntry,
    ScanReport,
    ScanStatus,
    SubjectRuleIn,
    SubjectRuleOut,
    TypeChoice,
    TypeSummary,
    UnmappedSubject,
)
from nats_lens.domain.protoschemas.store import ProtoStore

MAX_UNMAPPED_SUBJECTS: Final = 200
"""A bounded ring of curiosities, not a log. Least-recently-seen is dropped."""

_MIDDOT: Final = "·"


class SchemaError(Exception):
    """A registry operation the operator can fix. The message says how."""


class Conflict(SchemaError):
    """The registry already holds something that would collide."""


class NotFound(SchemaError):
    pass


# ---------------------------------------------------------------- the samples


class _Unmapped:
    __slots__ = ("first_seen", "hits", "last_seen", "subject")

    def __init__(self, subject: str, now: datetime) -> None:
        self.subject = subject
        self.hits = 0
        self.first_seen = now
        self.last_seen = now


class Samples:
    """What this process has watched go past. Provenance: `sampled`.

    Guarded by a lock because the capture fan-out records from whatever task the
    NATS client delivered on, while the API reads from a request handler.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rule_hits: dict[uuid.UUID, int] = {}
        self._type_last_seen: dict[str, datetime] = {}
        self._unmapped: dict[tuple[uuid.UUID | None, str], _Unmapped] = {}
        self.started_at = datetime.now(UTC)

    def record(
        self,
        decoded: Decoded,
        subject: str,
        *,
        server_id: uuid.UUID | None = None,
        rule_id: uuid.UUID | None = None,
    ) -> None:
        """Note one decoded message. Called by whatever ran the chain."""
        now = datetime.now(UTC)
        with self._lock:
            if rule_id is not None and decoded.resolved_by is ResolvedBy.SUBJECT_RULE:
                self._rule_hits[rule_id] = self._rule_hits.get(rule_id, 0) + 1
            if decoded.type_name is not None:
                self._type_last_seen[decoded.type_name] = now
            if decoded.unmapped_subject is not None:
                self._note_unmapped(server_id, decoded.unmapped_subject or subject, now)

    def rule_hits(self, rule_id: uuid.UUID) -> int:
        return self._rule_hits.get(rule_id, 0)

    def last_seen(self, type_full_name: str) -> datetime | None:
        return self._type_last_seen.get(type_full_name)

    def unmapped(self, server_id: uuid.UUID | None = None) -> list[UnmappedSubject]:
        with self._lock:
            seen = [
                entry
                for (scope, _), entry in self._unmapped.items()
                if server_id is None or scope == server_id or scope is None
            ]
        seen.sort(key=lambda entry: (-entry.hits, entry.subject))
        return [
            UnmappedSubject(
                subject=entry.subject,
                hits=entry.hits,
                first_seen=entry.first_seen,
                last_seen=entry.last_seen,
                suggested_pattern=suggested_pattern(entry.subject),
            )
            for entry in seen
        ]

    def forget_rule(self, rule_id: uuid.UUID) -> None:
        with self._lock:
            self._rule_hits.pop(rule_id, None)

    def _note_unmapped(self, server_id: uuid.UUID | None, subject: str, now: datetime) -> None:
        key = (server_id, subject)
        entry = self._unmapped.get(key)
        if entry is None:
            if len(self._unmapped) >= MAX_UNMAPPED_SUBJECTS:
                stalest = min(self._unmapped, key=lambda k: self._unmapped[k].last_seen)
                del self._unmapped[stalest]
            entry = _Unmapped(subject, now)
            self._unmapped[key] = entry
        entry.hits += 1
        entry.last_seen = now


# --------------------------------------------------------------- the registry


class SchemaRegistry:
    """The compiled descriptors and rules, cached until the tables change.

    One index for the whole process (descriptor sets are global) and one RuleSet
    per server, because a server-scoped rule must not decode another server's
    traffic.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._index: proto.DescriptorIndex | None = None
        self._rules: dict[uuid.UUID | None, RuleSet] = {}
        self._warnings: tuple[str, ...] = ()

    def invalidate(self) -> None:
        with self._lock:
            self._index = None
            self._rules.clear()
            self._warnings = ()

    @property
    def warnings(self) -> tuple[str, ...]:
        """Descriptors that were stored but could not be loaded into the pool."""
        return self._warnings

    async def index(self, repo: SchemaRepository) -> proto.DescriptorIndex:
        if self._index is not None:
            return self._index
        rows = await repo.list_descriptors()
        index = proto.DescriptorIndex()
        warnings: list[str] = []
        for row in rows:
            try:
                index.add(
                    row.file_descriptor_set,
                    imported_only=row.imported_only,
                    label=row.source_filename,
                )
            except proto.DescriptorError as exc:
                # One bad descriptor must not take the other ones down with it.
                warnings.append(f"{row.package} could not be loaded: {exc}")
        with self._lock:
            self._index = index
            self._warnings = tuple(warnings)
        return index

    async def rules(self, repo: SchemaRepository, server_id: uuid.UUID | None) -> RuleSet:
        if (cached := self._rules.get(server_id)) is not None:
            return cached
        rule_set = RuleSet(_to_rule(row) for row in await repo.list_rules(server_id))
        with self._lock:
            self._rules[server_id] = rule_set
        return rule_set


REGISTRY: Final = SchemaRegistry()
SAMPLES: Final = Samples()
"""Process-wide, because the decoding chain is process-wide. See app.py's
single-worker check: a second worker would count a different, partial sample."""


# ----------------------------------------------------------------- the service


class SchemaService:
    __slots__ = ("_registry", "_repo", "_samples", "_store")

    def __init__(
        self,
        repo: SchemaRepository,
        *,
        store: ProtoStore | None = None,
        registry: SchemaRegistry = REGISTRY,
        samples: Samples = SAMPLES,
    ) -> None:
        self._repo = repo
        # A store is always present: reading and writing definitions is not an
        # optional part of this service, and a default keeps every caller that
        # only wants rules from having to know about directories.
        self._store = store or ProtoStore(Path("data/uploads/protos"), None)
        self._registry = registry
        self._samples = samples

    async def list_types(self) -> list[TypeChoice]:
        """Every registered message type, flat, for the subject-rule picker.

        Imported-only descriptors are left out: they exist to satisfy someone
        else's imports and are never what a rule should point at.
        """
        counts = await self._repo.rule_counts_by_type()
        choices = [
            TypeChoice(
                full_name=t.full_name,
                package=descriptor.package,
                field_count=t.field_count,
                field_names=tuple(t.field_names),
                origin=Origin(descriptor.origin),
                source_filename=descriptor.source_filename,
                rule_count=counts.get(t.full_name, 0),
            )
            for descriptor in await self._repo.list_descriptors()
            if not descriptor.imported_only
            for t in descriptor.types
        ]
        choices.sort(key=lambda c: c.full_name)
        return choices

    # ------------------------------------------------------------ descriptors

    async def list_descriptors(self) -> list[DescriptorSummary]:
        rows = await self._repo.list_descriptors()
        counts = await self._repo.rule_counts_by_type()
        return [descriptor_summary(row, counts) for row in rows]

    async def get_descriptor(self, descriptor_id: uuid.UUID) -> DescriptorDetail:
        row = await self._repo.get_descriptor(descriptor_id)
        if row is None:
            raise NotFound(f"No descriptor {descriptor_id} is registered.")
        counts = await self._repo.rule_counts_by_type()
        return descriptor_detail(row, counts, self._samples)

    async def _register(
        self,
        *,
        filename: str,
        content: bytes,
        as_descriptor_set: bool,
        origin: str,
        source_path: str | None,
        note: str | None,
        imported_only: bool,
        tree_root: Path | None = None,
    ) -> Descriptor:
        """Compile if needed, check it loads, and replace whatever held the package.

        `tree_root` is the difference between the two sources: given one, protoc
        is pointed at the whole directory so imports between the operator's own
        files resolve. Without it a file is compiled alone, which is all an upload
        can offer.
        """
        if not content:
            raise SchemaError("The upload is empty.")

        if as_descriptor_set:
            file_descriptor_set = content
            protoc_version = None
        else:
            try:
                # protoc is a blocking C call; the event loop has messages to move.
                if tree_root is not None:
                    file_descriptor_set = await anyio.to_thread.run_sync(
                        proto.compile_proto_in_tree, tree_root, filename
                    )
                else:
                    file_descriptor_set = await anyio.to_thread.run_sync(
                        proto.compile_proto, filename, content
                    )
            except proto.DescriptorError as exc:
                # protoc's own diagnostics -- file, line, column -- are what the
                # operator needs, and they are already written to be read. Losing
                # them to a 500 is the difference between "line 3: import not
                # found" and "something went wrong".
                raise SchemaError(str(exc)) from exc
            protoc_version = await anyio.to_thread.run_sync(proto.protoc_version)

        try:
            compiled = proto.inspect_descriptor_set(file_descriptor_set, source_filename=filename)
            # Registering it in a throwaway pool now means a descriptor that cannot
            # be loaded is rejected here, not silently on the next message.
            probe = proto.DescriptorIndex()
            probe.add(file_descriptor_set, label=filename)
        except proto.DescriptorError as exc:
            raise SchemaError(str(exc)) from exc

        await self._reject_type_collisions(compiled)

        if (existing := await self._repo.descriptor_by_package(compiled.package)) is not None:
            if existing.origin == Origin.MOUNTED and origin == Origin.UPLOAD:
                # Letting an upload shadow a mounted package would not even hold:
                # the next scan re-registers from the tree and takes it back, so
                # the package would flip on every scan. Refuse, and say where the
                # file that owns it lives.
                raise Conflict(
                    f"{compiled.package} is already provided by the mounted directory "
                    f"({existing.source_path or existing.source_filename}). Change it there and "
                    "rescan -- an upload would be overwritten by the next scan."
                )
            if existing.origin == Origin.UPLOAD and origin == Origin.UPLOAD:
                self._store.delete_upload(
                    Path(existing.source_path) if existing.source_path else None
                )
            await self._repo.delete_descriptor(existing.id)

        row = await self._repo.add_descriptor(
            package=compiled.package,
            source_filename=filename,
            file_descriptor_set=file_descriptor_set,
            protoc_version=protoc_version,
            imported_only=imported_only,
            note=note,
            types=[(t.full_name, t.field_names) for t in compiled.types],
            origin=origin,
            source_path=source_path,
            content_sha256=hashlib.sha256(content).hexdigest(),
        )
        self._registry.invalidate()
        return row

    async def upload_descriptor(self, upload: DescriptorUpload) -> DescriptorDetail:
        """Register a file sent through the UI, and keep it on disk.

        Written to `proto_upload_dir` as well as the registry, so an upload is a
        file an operator can find, copy and back up rather than a blob locked
        inside the database. The write happens only after the content has been
        proved to compile and load -- an upload directory of files that do not
        work is worse than no directory.
        """
        content = _decode_base64(upload.content_b64, "content_b64")
        row = await self._register(
            filename=upload.filename,
            content=content,
            as_descriptor_set=upload.is_descriptor_set,
            origin=Origin.UPLOAD,
            source_path=None,
            note=upload.note,
            imported_only=upload.imported_only,
        )
        saved = self._store.save_upload(upload.filename, content)
        row.source_path = str(saved)
        counts = await self._repo.rule_counts_by_type()
        return descriptor_detail(row, counts, self._samples)

    async def scan_sources(self) -> ScanReport:
        """Bring the registry in line with what is on disk.

        Idempotent by digest: a file that has not changed is left alone, which
        keeps a rescan cheap enough to run on every start. A mounted descriptor
        whose file has gone is dropped, because the tree is the source of truth
        there and a row for a deleted file would decode messages nobody can
        account for.
        """
        files = await anyio.to_thread.run_sync(self._store.scan)
        known = {d.content_sha256: d for d in await self._repo.list_descriptors()}
        results: list[ScanEntry] = []
        seen_paths: set[str] = set()

        for found in files:
            seen_paths.add(str(found.path))
            existing = known.get(found.digest)
            if existing is not None and existing.origin == found.origin:
                results.append(
                    ScanEntry(
                        path=found.relative,
                        origin=found.origin,
                        package=existing.package,
                        status=ScanStatus.UNCHANGED,
                        detail=None,
                    )
                )
                continue
            try:
                row = await self._register(
                    filename=found.relative,
                    content=found.content,
                    as_descriptor_set=found.is_descriptor_set,
                    origin=found.origin,
                    source_path=str(found.path),
                    note=None,
                    imported_only=False,
                    tree_root=self._store.mount_dir
                    if found.origin is Origin.MOUNTED
                    else self._store.upload_dir,
                )
            except SchemaError as exc:
                # One bad file must not stop the rest. The report carries why.
                results.append(
                    ScanEntry(
                        path=found.relative,
                        origin=found.origin,
                        package=None,
                        status=ScanStatus.FAILED,
                        detail=str(exc),
                    )
                )
                continue
            results.append(
                ScanEntry(
                    path=found.relative,
                    origin=found.origin,
                    package=row.package,
                    status=ScanStatus.REGISTERED,
                    detail=None,
                )
            )

        # What is in the two directories is what is registered -- for uploads as
        # well as mounts, since an upload is a file in a directory too. Only rows
        # that recorded a path take part: a descriptor registered before uploads
        # were written to disk has `source_path` unset, and dropping those on the
        # first scan after an upgrade would delete a working registry.
        for descriptor in await self._repo.list_descriptors():
            if not descriptor.source_path or descriptor.source_path in seen_paths:
                continue
            origin = Origin(descriptor.origin)
            where = "mounted directory" if origin is Origin.MOUNTED else "upload directory"
            await self._repo.delete_descriptor(descriptor.id)
            results.append(
                ScanEntry(
                    path=descriptor.source_filename,
                    origin=origin,
                    package=descriptor.package,
                    status=ScanStatus.REMOVED,
                    detail=f"The file is no longer in the {where}.",
                )
            )
        self._registry.invalidate()
        return ScanReport(
            upload_dir=str(self._store.upload_dir),
            mount_dir=str(self._store.mount_dir) if self._store.mount_dir else None,
            mount_dir_present=bool(self._store.mount_dir and self._store.mount_dir.is_dir()),
            entries=tuple(results),
        )

    async def delete_descriptor(self, descriptor_id: uuid.UUID) -> None:
        existing = await self._repo.get_descriptor(descriptor_id)
        if existing is None:
            raise NotFound(f"No descriptor {descriptor_id} is registered.")
        if existing.origin == Origin.MOUNTED:
            raise Conflict(
                f"{existing.package} came from the mounted directory "
                f"({existing.source_path or existing.source_filename}), so it belongs to whoever "
                "mounted it. Remove the file from that directory and rescan; deleting the entry "
                "here would only bring it back on the next scan."
            )
        self._store.delete_upload(Path(existing.source_path) if existing.source_path else None)
        await self._repo.delete_descriptor(descriptor_id)
        self._registry.invalidate()

    async def _reject_type_collisions(self, compiled: proto.CompiledDescriptor) -> None:
        for type_info in compiled.types:
            owner = await self._repo.type_owner(type_info.full_name)
            if owner is not None and owner.descriptor.package != compiled.package:
                raise Conflict(
                    f"{type_info.full_name} is already declared by {owner.descriptor.package} "
                    f"({owner.descriptor.source_filename}). A message type can only come from one "
                    "descriptor; remove the other one first."
                )

    # ------------------------------------------------------------------ rules

    async def list_rules(self, server_id: uuid.UUID | None = None) -> list[SubjectRuleOut]:
        rows = await self._repo.list_rules(server_id)
        by_id = {row.id: row for row in rows}
        ordered = RuleSet(_to_rule(row) for row in rows)
        # Enabled rules first, in the order the chain considers them; disabled ones
        # after, so the list reads as "this is what would happen".
        out = [rule_out(by_id[rule.id], self._samples) for rule in ordered]
        out.extend(rule_out(row, self._samples) for row in rows if not row.enabled)
        return out

    async def create_rule(self, data: SubjectRuleIn) -> SubjectRuleOut:
        await self._validate_rule(data)
        row = await self._repo.add_rule(
            pattern=data.pattern,
            type_full_name=data.type_full_name,
            server_id=data.server_id,
            precedence=data.precedence,
            enabled=data.enabled,
        )
        self._registry.invalidate()
        return rule_out(row, self._samples)

    async def update_rule(self, rule_id: uuid.UUID, data: SubjectRuleIn) -> SubjectRuleOut:
        row = await self._repo.get_rule(rule_id)
        if row is None:
            raise NotFound(f"No rule {rule_id} exists.")
        await self._validate_rule(data)
        row.pattern = data.pattern
        row.type_full_name = data.type_full_name
        row.server_id = data.server_id
        row.precedence = data.precedence
        row.enabled = data.enabled
        await self._repo.save_rule(row)
        self._registry.invalidate()
        return rule_out(row, self._samples)

    async def delete_rule(self, rule_id: uuid.UUID) -> None:
        if not await self._repo.delete_rule(rule_id):
            raise NotFound(f"No rule {rule_id} exists.")
        self._samples.forget_rule(rule_id)
        self._registry.invalidate()

    async def _validate_rule(self, data: SubjectRuleIn) -> None:
        if (problem := validate_pattern(data.pattern)) is not None:
            raise SchemaError(problem)
        owner = await self._repo.type_owner(data.type_full_name)
        if owner is None:
            raise SchemaError(
                f"No registered descriptor declares {data.type_full_name}. Upload the .proto or "
                "its FileDescriptorSet first."
            )
        if owner.descriptor.imported_only:
            raise SchemaError(
                f"{data.type_full_name} comes from {owner.descriptor.package}, which is registered "
                "as imports only. Those types exist to satisfy other descriptors and are never "
                "matched directly by a rule."
            )

    # --------------------------------------------------------------- observed

    def unmapped(self, server_id: uuid.UUID | None = None) -> list[UnmappedSubject]:
        return self._samples.unmapped(server_id)

    async def decode_preview(self, request: DecodePreview) -> DecodePreviewResult:
        """Run the chain on bytes the operator pasted in. Publishes nothing."""
        payload = _decode_base64(request.payload_b64, "payload_b64")
        index = await self._registry.index(self._repo)
        rules = await self._registry.rules(self._repo, None)

        if request.type_full_name:
            decoded = chain.decode_as_type(payload, request.type_full_name, index, request.subject)
        else:
            decoded = chain.decode(payload, request.subject, request.headers, rules, index)
        if self._registry.warnings:
            decoded = _with_warnings(decoded, self._registry.warnings)

        matched = rules.match(request.subject) if request.subject else None
        row = await self._repo.get_rule(matched.id) if matched is not None else None
        return DecodePreviewResult(
            decoded=decoded,
            matched_rule=rule_out(row, self._samples) if row is not None else None,
        )

    async def ruleset(self, server_id: uuid.UUID | None) -> RuleSet:
        """The ordered rules for one server. What the capture fan-out decodes with."""
        return await self._registry.rules(self._repo, server_id)

    async def descriptor_index(self) -> proto.DescriptorIndex:
        return await self._registry.index(self._repo)


# ------------------------------------------------------------ response shaping


def resolution_steps() -> list[ResolutionStep]:
    """The five steps, in the order the chain runs them.

    Spelled out rather than derived from `ResolvedBy`, because this is the copy the
    Schemas screen shows and it has to read as prose, not as enum members.
    """
    return [
        ResolutionStep(
            n=1,
            name="Nats-Msg-Type header",
            description=(
                "A publisher naming its own type wins over everything else. The type still has "
                "to be one a registered descriptor declares."
            ),
        ),
        ResolutionStep(
            n=2,
            name="Subject rule",
            description=(
                "The most specific registered pattern that claims the subject. orders.new beats "
                "orders.* beats orders.>, whatever order they were added in."
            ),
        ),
        ResolutionStep(
            n=3,
            name="Content-Type header",
            description=(
                "application/json, application/msgpack or application/x-protobuf. Protobuf names "
                "a codec, not a message, so it only resolves here when the header also carries a "
                "messageType parameter."
            ),
        ),
        ResolutionStep(
            n=4,
            name="Shape of the bytes",
            description=(
                "Valid JSON, valid MessagePack or valid UTF-8 text, in that order. msgspec reads "
                "the first two with no schema at all, which is why neither needs registering."
            ),
        ),
        ResolutionStep(
            n=5,
            name="Raw wire format",
            description=(
                "Field numbers, wire types and best-effort values. Always available, because "
                "every byte sequence has a reading as protobuf tags."
            ),
        ),
    ]


def descriptor_summary(row: Descriptor, rule_counts: dict[str, int]) -> DescriptorSummary:
    return DescriptorSummary(
        id=row.id,
        package=row.package,
        source_filename=row.source_filename,
        protoc_version=row.protoc_version,
        size_bytes=len(row.file_descriptor_set),
        imported_only=row.imported_only,
        type_count=len(row.types),
        rule_count=sum(rule_counts.get(t.full_name, 0) for t in row.types),
        registered_at=row.created_at,
        origin=Origin(row.origin),
        source_path=row.source_path,
        note=row.note,
    )


def descriptor_detail(
    row: Descriptor, rule_counts: dict[str, int], samples: Samples
) -> DescriptorDetail:
    return DescriptorDetail(
        id=row.id,
        package=row.package,
        source_filename=row.source_filename,
        protoc_version=row.protoc_version,
        size_bytes=len(row.file_descriptor_set),
        imported_only=row.imported_only,
        registered_at=row.created_at,
        description=_description(row),
        types=tuple(
            type_summary(message_type, rule_counts, samples)
            for message_type in sorted(row.types, key=lambda t: t.full_name)
        ),
        origin=Origin(row.origin),
        source_path=row.source_path,
    )


def type_summary(row: MessageType, rule_counts: dict[str, int], samples: Samples) -> TypeSummary:
    return TypeSummary(
        full_name=row.full_name,
        field_names=tuple(row.field_names),
        field_count=row.field_count,
        rule_count=rule_counts.get(row.full_name, 0),
        last_seen=samples.last_seen(row.full_name),
    )


def rule_out(row: SubjectRule, samples: Samples) -> SubjectRuleOut:
    return SubjectRuleOut(
        id=row.id,
        pattern=row.pattern,
        type_full_name=row.type_full_name,
        server_id=row.server_id,
        precedence=row.precedence,
        specificity=specificity(row.pattern),
        enabled=row.enabled,
        hits=samples.rule_hits(row.id),
    )


def _description(row: Descriptor) -> str:
    """The sentence under the descriptor's name on the design's detail card."""
    if row.imported_only:
        base = (
            f"Shared types referenced by the other descriptors {_MIDDOT} never matched directly "
            f"by a rule {_MIDDOT} {_size(len(row.file_descriptor_set))}"
        )
    else:
        compiler = f"protoc {row.protoc_version}" if row.protoc_version else "an external build"
        base = (
            f"Compiled from {row.source_filename} with {compiler} {_MIDDOT} "
            f"{_size(len(row.file_descriptor_set))} {_MIDDOT} "
            f"{len(row.types)} message {'type' if len(row.types) == 1 else 'types'}"
        )
    return f"{base} {_MIDDOT} {row.note}" if row.note else base


def _size(byte_count: int) -> str:
    if byte_count < 1024:
        return f"{byte_count} B"
    if byte_count < 1024 * 1024:
        return f"{byte_count / 1024:.1f} KB"
    return f"{byte_count / (1024 * 1024):.1f} MB"


def _to_rule(row: SubjectRule) -> Rule:
    return Rule(
        id=row.id,
        pattern=row.pattern,
        type_full_name=row.type_full_name,
        server_id=row.server_id,
        precedence=row.precedence,
        enabled=row.enabled,
    )


def _with_warnings(decoded: Decoded, extra: Sequence[str]) -> Decoded:
    return msgspec.structs.replace(decoded, warnings=(*decoded.warnings, *extra))


def _decode_base64(value: str, field: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SchemaError(f"{field} is not valid base64.") from exc
