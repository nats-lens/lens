"""Key-Value buckets. OWNER: agent B4-streams.

Thin by design -- see domain/monitor/controller.py.
"""

from __future__ import annotations

import uuid

from litestar import Controller, delete, get, post, put
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath, FromQuery

from nats_lens.domain.kv.schemas import (
    BucketCreate,
    BucketSummary,
    KvEntry,
    KvKeyPage,
    KvPut,
)
from nats_lens.domain.kv.service import KeyValueService, provide_kv


class KeyValueController(Controller):
    path = "/api/servers/{server_id:uuid}/kv"
    tags = ["kv"]
    dependencies = {"kv": Provide(provide_kv, sync_to_thread=False)}

    @get("/")
    async def list_buckets(
        self, server_id: FromPath[uuid.UUID], kv: NamedDependency[KeyValueService]
    ) -> list[BucketSummary]:
        return await kv.list_buckets(server_id)

    @post("/", status_code=201)
    async def create_bucket(
        self,
        server_id: FromPath[uuid.UUID],
        data: BucketCreate,
        kv: NamedDependency[KeyValueService],
    ) -> BucketSummary:
        return await kv.create_bucket(server_id, data)

    @delete("/{bucket:str}")
    async def delete_bucket(
        self,
        server_id: FromPath[uuid.UUID],
        bucket: FromPath[str],
        kv: NamedDependency[KeyValueService],
    ) -> None:
        await kv.delete_bucket(server_id, bucket)

    @get("/{bucket:str}/keys", summary="Key rows without values, so a big bucket stays cheap")
    async def list_keys(
        self,
        server_id: FromPath[uuid.UUID],
        bucket: FromPath[str],
        kv: NamedDependency[KeyValueService],
        filter: FromQuery[str | None] = None,
        limit: FromQuery[int] = 500,
    ) -> KvKeyPage:
        return await kv.list_keys(server_id, bucket, filter, limit)

    @get("/{bucket:str}/keys/{key:path}")
    async def get_entry(
        self,
        server_id: FromPath[uuid.UUID],
        bucket: FromPath[str],
        key: FromPath[str],
        kv: NamedDependency[KeyValueService],
    ) -> KvEntry:
        return await kv.get_entry(server_id, bucket, key.lstrip("/"))

    @put("/{bucket:str}/keys/{key:path}", summary="Compare-and-set when last_revision is given")
    async def put_entry(
        self,
        server_id: FromPath[uuid.UUID],
        bucket: FromPath[str],
        key: FromPath[str],
        data: KvPut,
        kv: NamedDependency[KeyValueService],
    ) -> KvEntry:
        return await kv.put_entry(server_id, bucket, key.lstrip("/"), data)

    @delete("/{bucket:str}/keys/{key:path}")
    async def delete_entry(
        self,
        server_id: FromPath[uuid.UUID],
        bucket: FromPath[str],
        key: FromPath[str],
        kv: NamedDependency[KeyValueService],
        purge: FromQuery[bool] = False,
    ) -> None:
        await kv.delete_entry(server_id, bucket, key.lstrip("/"), purge)

    @get("/{bucket:str}/history/{key:path}")
    async def history(
        self,
        server_id: FromPath[uuid.UUID],
        bucket: FromPath[str],
        key: FromPath[str],
        kv: NamedDependency[KeyValueService],
    ) -> list[KvEntry]:
        return await kv.history(server_id, bucket, key.lstrip("/"))
