"""The object store: buckets, objects, and streamed content.

An object-store bucket is a JetStream stream named `OBJ_<bucket>`, the same
convention every NATS client relies on, so the bucket list is a stream list
filtered by prefix rather than a separate API.

Two behaviours here exist because the underlying library forces them:

  * `ObjectStore.list()` raises `NotFoundError` when a bucket holds no objects.
    An empty bucket is not an error, so that is caught and turned into `[]`.
  * nats-py can *read* a link object but has no API to *create* one. Rather than
    hand-write object metadata into the stream and risk producing something other
    clients misread, link creation says plainly that it is not available.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Protocol, cast

import msgspec.structs
from litestar.di import NamedDependency
from litestar.exceptions import ClientException, NotFoundException
from nats.js import JetStreamContext, api
from nats.js import errors as js_errors
from nats.js.object_store import ObjectStore
from sqlalchemy.ext.asyncio import AsyncSession

from nats_lens.conn.manager import ConnectionManager
from nats_lens.domain.jetstream.schemas import Storage
from nats_lens.domain.nats_access import (
    enum_value,
    iter_stream_infos,
    jetstream,
    translate_nats_errors,
)
from nats_lens.domain.objects.schemas import (
    ObjectBucketCreate,
    ObjectBucketSummary,
    ObjectInfo,
    ObjectLink,
    ObjectMetaUpdate,
)


class ChunkReader(Protocol):
    """What nats-py's `put` actually needs from its input.

    It is typed as `BufferedIOBase`, but the implementation only ever calls
    `readinto` -- which is what lets a Litestar `UploadFile`'s spooled temporary
    file be handed straight to it. Naming the real requirement here beats casting
    at the call site and pretending the file is something it is not.
    """

    def readinto(self, buffer: bytearray, /) -> int | None: ...


OBJ_STREAM_PREFIX = "OBJ_"
_DEFAULT_CHUNK_SIZE = 128 * 1024
"""What the server uses when a bucket does not name one."""
_DOWNLOAD_CHUNK = 64 * 1024


def bucket_name(stream_name: str) -> str:
    return stream_name.removeprefix(OBJ_STREAM_PREFIX)


def _usage(bytes_used: int, max_bytes: int) -> float | None:
    """Fraction of the byte limit in use, 0 to 1. None when unlimited -- there is
    nothing for a fraction to be a fraction of."""
    if max_bytes <= 0:
        return None
    return round(min(1.0, bytes_used / max_bytes), 4)


def bucket_summary(info: api.StreamInfo) -> ObjectBucketSummary:
    config = info.config
    state = info.state
    max_bytes = config.max_bytes if config.max_bytes is not None else -1
    return ObjectBucketSummary(
        name=bucket_name(config.name or ""),
        stream_name=config.name or "",
        # One message per chunk plus one per object metadata entry, so the message
        # count is not the object count. `objects` is filled in by the caller when
        # it has actually listed them; here it is the honest lower bound of zero.
        objects=0,
        bytes=state.bytes,
        storage=Storage(enum_value(config.storage, "file")),
        replicas=config.num_replicas or 1,
        sealed=bool(config.sealed),
        max_chunk_size=_DEFAULT_CHUNK_SIZE,
        ttl_seconds=config.max_age or None,
        usage=_usage(state.bytes, max_bytes),
        description=config.description,
        compressed=bool(getattr(config, "compression", None)),
    )


def _digest(raw: str | None) -> str:
    """The server reports `SHA-256=<base64url>`. Pass it through untouched."""
    return raw or ""


def _modified(mtime: str | None) -> datetime:
    if not mtime:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(mtime.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)


def _headers(raw: dict[str, list[str]] | dict[str, str] | None) -> dict[str, str]:
    """Object headers arrive as a multi-value map; the UI shows one value per key."""
    if not raw:
        return {}
    flat: dict[str, str] = {}
    for key, value in raw.items():
        flat[key] = value[0] if isinstance(value, list) and value else str(value)
    return flat


def object_info(info: api.ObjectInfo) -> ObjectInfo:
    headers = _headers(info.headers)
    return ObjectInfo(
        name=info.name,
        bucket=info.bucket,
        size=info.size or 0,
        chunks=info.chunks or 0,
        digest=_digest(info.digest),
        modified=_modified(info.mtime),
        deleted=bool(info.deleted),
        description=info.description,
        headers=headers,
        content_type=headers.get("Content-Type"),
        nuid=info.nuid,
    )


class ObjectStoreService:
    """Everything the Object store screen needs."""

    def __init__(self, connections: ConnectionManager, session: AsyncSession) -> None:
        self._connections = connections
        self._session = session

    async def _store(self, server_id: uuid.UUID, bucket: str) -> ObjectStore:
        async with jetstream(self._connections, server_id, f"/objects/{bucket}") as js:
            try:
                return await js.context.object_store(bucket)
            except js_errors.NotFoundError as exc:
                raise NotFoundException(detail=f"No object bucket named {bucket!r}.") from exc

    # ------------------------------------------------------------------ buckets

    async def list_buckets(self, server_id: uuid.UUID) -> list[ObjectBucketSummary]:
        buckets: list[ObjectBucketSummary] = []
        async with jetstream(self._connections, server_id, "/objects") as js:
            async for info in iter_stream_infos(js.manager):
                if not (info.config.name or "").startswith(OBJ_STREAM_PREFIX):
                    continue
                summary = bucket_summary(info)
                objects = await self._count_objects(js.context, summary.name)
                buckets.append(msgspec.structs.replace(summary, objects=objects))
        buckets.sort(key=lambda b: b.name)
        return buckets

    async def _count_objects(self, js: JetStreamContext, bucket: str) -> int:
        """How many live objects a bucket holds.

        Chunks and metadata share the stream, so the message count would be wrong.
        """
        try:
            store = await js.object_store(bucket)
            return len(await store.list(ignore_deletes=True))
        except js_errors.NotFoundError:
            # Either the bucket vanished between listing and counting, or it is
            # empty -- `list()` raises for both. Neither is an error worth showing.
            return 0

    async def create_bucket(
        self, server_id: uuid.UUID, data: ObjectBucketCreate
    ) -> ObjectBucketSummary:
        config = api.ObjectStoreConfig(
            bucket=data.name,
            description=data.description,
            ttl=data.ttl_seconds,
            max_bytes=data.max_bytes if data.max_bytes > 0 else None,
            storage=api.StorageType(data.storage.value),
            replicas=data.replicas,
        )
        async with jetstream(self._connections, server_id, "/objects") as js:
            await js.context.create_object_store(data.name, config)
        return await self.get_bucket(server_id, data.name)

    async def get_bucket(self, server_id: uuid.UUID, bucket: str) -> ObjectBucketSummary:
        for summary in await self.list_buckets(server_id):
            if summary.name == bucket:
                return summary
        raise NotFoundException(detail=f"No object bucket named {bucket!r}.")

    async def seal_bucket(self, server_id: uuid.UUID, bucket: str) -> ObjectBucketSummary:
        """Sealing is permanent: the bucket becomes read-only for good."""
        store = await self._store(server_id, bucket)
        async with translate_nats_errors(f"/objects/{bucket}/seal"):
            await store.seal()
        return await self.get_bucket(server_id, bucket)

    # ------------------------------------------------------------------ objects

    async def list_objects(self, server_id: uuid.UUID, bucket: str) -> list[ObjectInfo]:
        """An empty bucket is `[]`.

        nats-py raises `NotFoundError` when a bucket holds nothing, which would
        otherwise surface as a 404 for a bucket that plainly exists.
        """
        store = await self._store(server_id, bucket)
        async with translate_nats_errors(f"/objects/{bucket}/objects"):
            try:
                infos = await store.list(ignore_deletes=True)
            except js_errors.NotFoundError:
                return []
        return sorted((object_info(i) for i in infos), key=lambda o: o.name)

    async def get_object_info(self, server_id: uuid.UUID, bucket: str, name: str) -> ObjectInfo:
        store = await self._store(server_id, bucket)
        async with translate_nats_errors(f"/objects/{bucket}/objects/{name}"):
            try:
                info = await store.get_info(name)
            except js_errors.ObjectNotFoundError as exc:
                raise NotFoundException(
                    detail=f"No object named {name!r} in bucket {bucket!r}."
                ) from exc
        return object_info(info)

    async def download(
        self, server_id: uuid.UUID, bucket: str, name: str
    ) -> tuple[ObjectInfo, AsyncIterator[bytes]]:
        """The object's metadata, and its bytes as a stream.

        nats-py assembles the whole object in memory before returning it, so the
        chunking here is about not holding a second copy in the response buffer
        rather than about true end-to-end streaming.
        """
        store = await self._store(server_id, bucket)
        async with translate_nats_errors(f"/objects/{bucket}/objects/{name}/content"):
            try:
                result = await store.get(name)
            except js_errors.ObjectNotFoundError as exc:
                raise NotFoundException(
                    detail=f"No object named {name!r} in bucket {bucket!r}."
                ) from exc

        info = object_info(result.info)
        data = result.data or b""

        async def chunks() -> AsyncIterator[bytes]:
            for start in range(0, len(data), _DOWNLOAD_CHUNK):
                yield data[start : start + _DOWNLOAD_CHUNK]

        return info, chunks()

    async def upload(
        self,
        server_id: uuid.UUID,
        bucket: str,
        name: str,
        data: ChunkReader,
        *,
        description: str | None = None,
        content_type: str | None = None,
    ) -> ObjectInfo:
        """Write an object, streaming from the request rather than buffering it.

        nats-py reads through `readinto` in chunk-sized reads, and Litestar hands
        us a spooled temporary file, so a large object never exists in memory in
        one piece at either end.
        """
        store = await self._store(server_id, bucket)
        headers = {"Content-Type": content_type} if content_type else None
        meta = api.ObjectMeta(name=name, description=description, headers=headers)
        async with translate_nats_errors(f"/objects/{bucket}/objects/{name}"):
            try:
                # `put` declares BufferedIOBase but only calls readinto; see ChunkReader.
                info = await store.put(name, cast("io.BufferedIOBase", data), meta)
            except js_errors.BadRequestError as exc:
                raise ClientException(
                    detail=(
                        f"Bucket {bucket!r} is sealed, so nothing more can be written to it. "
                        "Sealing is permanent."
                    )
                ) from exc
        return object_info(info)

    async def update_meta(
        self, server_id: uuid.UUID, bucket: str, name: str, changes: ObjectMetaUpdate
    ) -> ObjectInfo:
        """Rename or re-describe an object without touching its bytes."""
        store = await self._store(server_id, bucket)
        current = await self.get_object_info(server_id, bucket, name)

        meta = api.ObjectMeta(
            name=changes.name or current.name,
            description=(
                changes.description if changes.description is not None else current.description
            ),
            headers={**current.headers, **(changes.headers or {})} or None,
        )
        async with translate_nats_errors(f"/objects/{bucket}/objects/{name}"):
            try:
                await store.update_meta(name, meta)
            except js_errors.ObjectNotFoundError as exc:
                raise NotFoundException(
                    detail=f"No object named {name!r} in bucket {bucket!r}."
                ) from exc
            except js_errors.BadRequestError as exc:
                raise ClientException(
                    detail=f"Bucket {bucket!r} is sealed, so its objects cannot be changed."
                ) from exc
        return await self.get_object_info(server_id, bucket, meta.name or name)

    async def delete_object(self, server_id: uuid.UUID, bucket: str, name: str) -> None:
        store = await self._store(server_id, bucket)
        async with translate_nats_errors(f"/objects/{bucket}/objects/{name}"):
            try:
                await store.delete(name)
            except js_errors.ObjectNotFoundError as exc:
                raise NotFoundException(
                    detail=f"No object named {name!r} in bucket {bucket!r}."
                ) from exc
            except js_errors.BadRequestError as exc:
                raise ClientException(
                    detail=(
                        f"Bucket {bucket!r} is sealed, so its objects cannot be deleted. "
                        "Sealing is permanent."
                    )
                ) from exc

    async def create_link(self, server_id: uuid.UUID, bucket: str, data: ObjectLink) -> ObjectInfo:
        """Not available, and it says so rather than faking it.

        nats-py 2.15 resolves a link on `get()` but has no API to create one. The
        alternative would be writing object metadata into the stream by hand, which
        risks producing an object other NATS clients read differently -- a worse
        outcome than not offering the button.
        """
        await self._store(server_id, bucket)
        raise ClientException(
            status_code=501,
            detail=(
                "nats-lens cannot create link objects. The nats-py client resolves "
                "links when reading but exposes no way to create them, and writing "
                "the metadata by hand risks producing an object other NATS clients "
                "would read differently. Existing links are readable here."
            ),
        )


def provide_objects(
    connections: NamedDependency[ConnectionManager], session: NamedDependency[AsyncSession]
) -> ObjectStoreService:
    return ObjectStoreService(connections, session)
