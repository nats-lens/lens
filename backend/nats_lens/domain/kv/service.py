"""Key-Value buckets: list, read, write, and the history the design shows.

A KV bucket is a JetStream stream named `KV_<bucket>` with one subject per key
(`$KV.<bucket>.<key>`); that convention is public across every NATS client, Go's
included, so relying on it here is no more fragile than relying on the JetStream
API itself. `KeyValue._stream` / `._pre` / `._direct` are read directly for the
same reason `conn/connection.py` reads `nc._server_info` -- nats-py has no public
accessor for them, and the alternative is a second round trip for information the
object already has in hand.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

from litestar.di import NamedDependency
from litestar.exceptions import ClientException
from nats.js import JetStreamContext, JetStreamManager, api
from nats.js import errors as js_errors
from nats.js.kv import KeyValue
from sqlalchemy.ext.asyncio import AsyncSession

from nats_lens.conn.errors import NatsProblem
from nats_lens.conn.manager import ConnectionManager
from nats_lens.domain.common import ProblemDetail
from nats_lens.domain.jetstream.schemas import Storage
from nats_lens.domain.kv.schemas import (
    BucketCreate,
    BucketSummary,
    KvEntry,
    KvKeyPage,
    KvKeyRow,
    KvOperation,
    KvPut,
)
from nats_lens.domain.nats_access import (
    decode_payload,
    enum_value,
    iter_stream_infos,
    jetstream,
)

KV_STREAM_PREFIX = "KV_"
_LARGE_BUCKET = 1_000
"""Past this many keys, the key page carries the design's honest warning."""


# ---------------------------------------------------------------------- mapping


def bucket_name(stream_name: str) -> str:
    return stream_name.removeprefix(KV_STREAM_PREFIX)


def _bucket_usage(bytes_used: int, max_bytes: int) -> float | None:
    """Fraction of the byte limit in use, 0 to 1. None when unlimited -- there is
    nothing for a fraction to be a fraction of."""
    if max_bytes <= 0:
        return None
    return round(min(1.0, bytes_used / max_bytes), 4)


def bucket_summary(info: api.StreamInfo) -> BucketSummary:
    config = info.config
    state = info.state
    max_bytes = config.max_bytes if config.max_bytes is not None else -1
    return BucketSummary(
        name=bucket_name(config.name or ""),
        stream_name=config.name or "",
        values=state.messages,
        bytes=state.bytes,
        history=config.max_msgs_per_subject if config.max_msgs_per_subject > 0 else 1,
        ttl_seconds=config.max_age or None,
        max_value_size=config.max_msg_size if config.max_msg_size is not None else -1,
        storage=Storage(enum_value(config.storage, "file")),
        replicas=config.num_replicas or 1,
        usage=_bucket_usage(state.bytes, max_bytes),
        description=config.description,
        compressed=enum_value(config.compression, "none") == "s2",
    )


def _kv_operation(headers: Mapping[str, str] | None) -> KvOperation:
    """PUT unless the message says otherwise -- a legacy `KV-Operation` header,
    or the `Nats-Marker-Reason` a 2.11+ server places for a TTL/age expiry."""
    if not headers:
        return KvOperation.PUT
    op = headers.get("KV-Operation")
    if op in ("DEL", "PURGE"):
        return KvOperation(op)
    reason = headers.get("Nats-Marker-Reason")
    if reason == "Remove":
        return KvOperation.DEL
    if reason in ("MaxAge", "Purge"):
        return KvOperation.PURGE
    return KvOperation.PUT


def _key_note(total: int) -> str:
    if total == 0:
        return "No keys yet."
    noun = "key" if total == 1 else "keys"
    if total > _LARGE_BUCKET:
        return f"{total:,} {noun} — listing walks the bucket, so filter first."
    return f"{total:,} {noun}."


def _key_row(entry: KeyValue.Entry) -> KvKeyRow:
    created = entry.created if isinstance(entry.created, datetime) else datetime.now(UTC)
    return KvKeyRow(
        key=entry.key,
        revision=entry.revision or 0,
        size=len(entry.value) if entry.value else 0,
        created=created,
        operation=KvOperation(entry.operation) if entry.operation else KvOperation.PUT,
    )


# ---------------------------------------------------------------------- service


class KeyValueService:
    """Everything behind `/api/servers/{id}/kv`."""

    def __init__(self, connections: ConnectionManager, session: AsyncSession) -> None:
        self._connections = connections
        self._session = session

    @staticmethod
    async def _bucket(js: JetStreamContext, bucket: str) -> KeyValue:
        # `key_value` raises `BucketNotFoundError` for a missing bucket -- already
        # mapped to 404 "No such bucket" by conn/errors.py.
        return await js.key_value(bucket)

    async def list_buckets(self, server_id: uuid.UUID) -> list[BucketSummary]:
        async with jetstream(self._connections, server_id, f"/api/servers/{server_id}/kv") as js:
            return [
                bucket_summary(info)
                async for info in iter_stream_infos(js.manager)
                if (info.config.name or "").startswith(KV_STREAM_PREFIX)
            ]

    async def create_bucket(self, server_id: uuid.UUID, data: BucketCreate) -> BucketSummary:
        async with jetstream(self._connections, server_id, f"/api/servers/{server_id}/kv") as js:
            config = api.KeyValueConfig(
                bucket=data.name,
                description=data.description,
                max_value_size=data.max_value_size,
                history=data.history,
                ttl=data.ttl_seconds,
                max_bytes=data.max_bytes,
                storage=api.StorageType(data.storage.value),
                replicas=data.replicas,
            )
            kv = await js.context.create_key_value(config)
            status = await kv.status()
            return bucket_summary(status.stream_info)

    async def delete_bucket(self, server_id: uuid.UUID, bucket: str) -> None:
        instance = f"/api/servers/{server_id}/kv/{bucket}"
        async with jetstream(self._connections, server_id, instance) as js:
            await js.context.delete_key_value(bucket)

    async def list_keys(
        self, server_id: uuid.UUID, bucket: str, filter: str | None, limit: int
    ) -> KvKeyPage:
        """Walks the bucket -- there is no cheaper way to learn revision, size,
        creation time and operation for every key at once. `note` says so."""
        instance = f"/api/servers/{server_id}/kv/{bucket}/keys"
        async with jetstream(self._connections, server_id, instance) as js:
            kv = await self._bucket(js.context, bucket)
            watcher = await kv.watch(filter or ">", ignore_deletes=False, include_history=False)
            rows: list[KeyValue.Entry] = []
            try:
                async for entry in watcher:
                    if entry is None:
                        break
                    rows.append(entry)
            finally:
                await watcher.stop()

        rows.sort(key=lambda e: e.key)
        total = len(rows)
        capped = rows[: max(1, limit)]
        return KvKeyPage(
            keys=tuple(_key_row(e) for e in capped),
            total=total,
            truncated=total > len(capped),
            note=_key_note(total),
        )

    async def _raw_entry(self, jsm: JetStreamManager, kv: KeyValue, key: str) -> api.RawStreamMsg:
        try:
            return await jsm.get_msg(kv._stream, subject=f"{kv._pre}{key}", direct=kv._direct)
        except js_errors.NotFoundError as exc:
            raise js_errors.KeyNotFoundError() from exc

    async def _entry_from_raw(
        self, server_id: uuid.UUID, key: str, raw: api.RawStreamMsg
    ) -> KvEntry:
        op = _kv_operation(raw.headers)
        payload = raw.data or b""
        decoded = None
        payload_b64 = None
        if op is KvOperation.PUT:
            subject = raw.subject or ""
            decoded = await decode_payload(
                self._session, server_id, payload, subject, dict(raw.headers or {})
            )
            payload_b64 = base64.b64encode(payload).decode()
        return KvEntry(
            key=key,
            revision=raw.seq or 0,
            created=raw.time or datetime.now(UTC),
            operation=op,
            size=len(payload) if op is KvOperation.PUT else 0,
            payload_b64=payload_b64,
            decoded=decoded,
        )

    async def get_entry(self, server_id: uuid.UUID, bucket: str, key: str) -> KvEntry:
        instance = f"/api/servers/{server_id}/kv/{bucket}/keys/{key}"
        async with jetstream(self._connections, server_id, instance) as js:
            kv = await self._bucket(js.context, bucket)
            raw = await self._raw_entry(js.manager, kv, key)
            if _kv_operation(raw.headers) is not KvOperation.PUT:
                raise js_errors.KeyNotFoundError()
            return await self._entry_from_raw(server_id, key, raw)

    async def _cas_conflict(
        self, jsm: JetStreamManager, kv: KeyValue, key: str, instance: str
    ) -> NatsProblem:
        """The current revision, fetched fresh, so the message names what
        actually happened rather than just that a mismatch occurred."""
        try:
            raw = await jsm.get_msg(kv._stream, subject=f"{kv._pre}{key}", direct=kv._direct)
            current = raw.seq or 0
            state = "present" if _kv_operation(raw.headers) is KvOperation.PUT else "deleted"
        except js_errors.NotFoundError:
            current, state = 0, "never written"
        detail = (
            f"Someone else wrote to {key} first: it is now at revision {current} ({state}), "
            "not the revision this write expected. Read it again before retrying."
        )
        return NatsProblem(
            ProblemDetail(
                type="/problems/kv-revision-conflict",
                title="Key-Value write conflict",
                status=409,
                detail=detail,
                instance=instance,
                nats_error="nats.js.errors.KeyWrongLastSequenceError",
            )
        )

    async def put_entry(self, server_id: uuid.UUID, bucket: str, key: str, data: KvPut) -> KvEntry:
        """A write with `last_revision` is a compare-and-set: `kv.update(key,
        value, last=rev)` fails instead of clobbering a concurrent writer."""
        instance = f"/api/servers/{server_id}/kv/{bucket}/keys/{key}"
        try:
            value = base64.b64decode(data.value_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ClientException(detail="value_b64 is not valid base64.") from exc

        async with jetstream(self._connections, server_id, instance) as js:
            kv = await self._bucket(js.context, bucket)
            try:
                if data.last_revision is not None:
                    await kv.update(key, value, last=data.last_revision)
                else:
                    await kv.put(key, value)
            except js_errors.KeyWrongLastSequenceError as exc:
                raise await self._cas_conflict(js.manager, kv, key, instance) from exc
        return await self.get_entry(server_id, bucket, key)

    async def delete_entry(self, server_id: uuid.UUID, bucket: str, key: str, purge: bool) -> None:
        instance = f"/api/servers/{server_id}/kv/{bucket}/keys/{key}"
        async with jetstream(self._connections, server_id, instance) as js:
            kv = await self._bucket(js.context, bucket)
            if purge:
                await kv.purge(key)
            else:
                await kv.delete(key)

    async def _entry_from_history(
        self, server_id: uuid.UUID, bucket: str, entry: KeyValue.Entry
    ) -> KvEntry:
        op = KvOperation(entry.operation) if entry.operation else KvOperation.PUT
        payload = entry.value or b""
        decoded = None
        payload_b64 = None
        if op is KvOperation.PUT:
            # `kv.history()` does not surface the original headers, so the header-
            # and Content-Type-based steps of the chain cannot fire here; sniffing
            # and the wire fallback still do.
            subject = f"$KV.{bucket}.{entry.key}"
            decoded = await decode_payload(self._session, server_id, payload, subject, {})
            payload_b64 = base64.b64encode(payload).decode()
        created = entry.created if isinstance(entry.created, datetime) else datetime.now(UTC)
        return KvEntry(
            key=entry.key,
            revision=entry.revision or 0,
            created=created,
            operation=op,
            size=len(payload) if op is KvOperation.PUT else 0,
            payload_b64=payload_b64,
            decoded=decoded,
            delta=entry.delta or 0,
        )

    async def history(self, server_id: uuid.UUID, bucket: str, key: str) -> list[KvEntry]:
        instance = f"/api/servers/{server_id}/kv/{bucket}/history/{key}"
        async with jetstream(self._connections, server_id, instance) as js:
            kv = await self._bucket(js.context, bucket)
            try:
                entries = await kv.history(key)
            except js_errors.NoKeysError:
                return []
            return [await self._entry_from_history(server_id, bucket, e) for e in entries]


def provide_kv(
    connections: NamedDependency[ConnectionManager], session: NamedDependency[AsyncSession]
) -> KeyValueService:
    return KeyValueService(connections, session)
