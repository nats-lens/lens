"""Object store. OWNER: agent B4-streams.

Thin by design -- see domain/monitor/controller.py.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from litestar import Controller, delete, get, patch, post
from litestar.datastructures import UploadFile
from litestar.di import NamedDependency, Provide
from litestar.enums import RequestEncodingType
from litestar.params import Body, FromPath, FromQuery
from litestar.response import Stream

from nats_lens.domain.objects.schemas import (
    ObjectBucketCreate,
    ObjectBucketSummary,
    ObjectInfo,
    ObjectLink,
    ObjectMetaUpdate,
)
from nats_lens.domain.objects.service import ObjectStoreService, provide_objects


class ObjectStoreController(Controller):
    path = "/api/servers/{server_id:uuid}/objects"
    tags = ["objects"]
    dependencies = {"objects": Provide(provide_objects, sync_to_thread=False)}

    @get("/")
    async def list_buckets(
        self, server_id: FromPath[uuid.UUID], objects: NamedDependency[ObjectStoreService]
    ) -> list[ObjectBucketSummary]:
        return await objects.list_buckets(server_id)

    @post("/", status_code=201)
    async def create_bucket(
        self,
        server_id: FromPath[uuid.UUID],
        data: ObjectBucketCreate,
        objects: NamedDependency[ObjectStoreService],
    ) -> ObjectBucketSummary:
        return await objects.create_bucket(server_id, data)

    @post("/{bucket:str}/seal", summary="Permanent: a sealed bucket is read-only for good")
    async def seal_bucket(
        self,
        server_id: FromPath[uuid.UUID],
        bucket: FromPath[str],
        objects: NamedDependency[ObjectStoreService],
    ) -> ObjectBucketSummary:
        return await objects.seal_bucket(server_id, bucket)

    @get(
        "/{bucket:str}/objects",
        summary="Objects in a bucket. An empty bucket is [], not the NotFoundError nats-py raises",
    )
    async def list_objects(
        self,
        server_id: FromPath[uuid.UUID],
        bucket: FromPath[str],
        objects: NamedDependency[ObjectStoreService],
    ) -> list[ObjectInfo]:
        return await objects.list_objects(server_id, bucket)

    @get("/{bucket:str}/objects/{name:path}")
    async def get_object_info(
        self,
        server_id: FromPath[uuid.UUID],
        bucket: FromPath[str],
        name: FromPath[str],
        objects: NamedDependency[ObjectStoreService],
    ) -> ObjectInfo:
        return await objects.get_object_info(server_id, bucket, name.lstrip("/"))

    @get(
        "/{bucket:str}/download/{name:path}",
        summary="The object's bytes, streamed. Never buffered whole.",
    )
    async def download(
        self,
        server_id: FromPath[uuid.UUID],
        bucket: FromPath[str],
        name: FromPath[str],
        objects: NamedDependency[ObjectStoreService],
    ) -> Stream:
        """A path of its own, not `/objects/{name}/content`.

        Object names may contain slashes, so `{name:path}` has to be greedy --
        which means it also swallows any suffix after it. `.../objects/x/content`
        resolved to an object literally named `x/content` and 404'd. Giving
        download its own segment removes the ambiguity entirely.
        """
        info, chunks = await objects.download(server_id, bucket, name.lstrip("/"))
        return Stream(
            chunks,
            media_type=info.content_type or "application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{info.name}"',
                "Content-Length": str(info.size),
                "X-Nats-Digest": info.digest,
            },
        )

    @post(
        "/{bucket:str}/objects",
        status_code=201,
        summary="Upload an object, streamed from the request body",
    )
    async def upload(
        self,
        server_id: FromPath[uuid.UUID],
        bucket: FromPath[str],
        objects: NamedDependency[ObjectStoreService],
        data: Annotated[UploadFile, Body(media_type=RequestEncodingType.MULTI_PART)],
        name: FromQuery[str | None] = None,
    ) -> ObjectInfo:
        """Multipart rather than a base64 field.

        An object store holds files, and files are large; base64 in a JSON body
        would inflate them by a third and force the whole thing into memory at
        both ends. `UploadFile` spools to disk, and nats-py reads it in chunks.
        """
        return await objects.upload(
            server_id,
            bucket,
            name or data.filename,
            data.file,  # a spooled temp file: exactly the ChunkReader nats-py needs
            content_type=data.content_type or None,
        )

    @patch(
        "/{bucket:str}/objects/{name:path}",
        summary="Rename or re-describe an object without rewriting its bytes",
    )
    async def update_meta(
        self,
        server_id: FromPath[uuid.UUID],
        bucket: FromPath[str],
        name: FromPath[str],
        data: ObjectMetaUpdate,
        objects: NamedDependency[ObjectStoreService],
    ) -> ObjectInfo:
        return await objects.update_meta(server_id, bucket, name.lstrip("/"), data)

    @post("/{bucket:str}/links", status_code=201)
    async def create_link(
        self,
        server_id: FromPath[uuid.UUID],
        bucket: FromPath[str],
        data: ObjectLink,
        objects: NamedDependency[ObjectStoreService],
    ) -> ObjectInfo:
        return await objects.create_link(server_id, bucket, data)

    @delete("/{bucket:str}/objects/{name:path}")
    async def delete_object(
        self,
        server_id: FromPath[uuid.UUID],
        bucket: FromPath[str],
        name: FromPath[str],
        objects: NamedDependency[ObjectStoreService],
    ) -> None:
        await objects.delete_object(server_id, bucket, name.lstrip("/"))
