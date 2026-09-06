"""JetStream streams, consumers, subjects, messages. OWNER: agent B4-streams.

Thin by design -- see domain/monitor/controller.py. Every mapping decision,
health heuristic and pagination detail lives in service.py where it can be
tested without a request.
"""

from __future__ import annotations

import uuid

from litestar import Controller, delete, get, patch, post
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath, FromQuery

from nats_lens.domain.jetstream.schemas import (
    ConsumerCreate,
    ConsumerPauseRequest,
    ConsumerSummary,
    ConsumerUpdate,
    MessageQuery,
    PurgeRequest,
    StoredMessage,
    StreamCreate,
    StreamDetail,
    StreamSummary,
    StreamUpdate,
    SubjectCount,
)
from nats_lens.domain.jetstream.service import JetStreamService, provide_jetstream


class JetStreamController(Controller):
    path = "/api/servers/{server_id:uuid}/jetstream"
    tags = ["jetstream"]
    dependencies = {"jetstream": Provide(provide_jetstream, sync_to_thread=False)}

    @get("/streams", summary="All streams (paged via streams_info_iterator)")
    async def list_streams(
        self, server_id: FromPath[uuid.UUID], jetstream: NamedDependency[JetStreamService]
    ) -> list[StreamSummary]:
        return await jetstream.list_streams(server_id)

    @post("/streams", status_code=201)
    async def create_stream(
        self,
        server_id: FromPath[uuid.UUID],
        data: StreamCreate,
        jetstream: NamedDependency[JetStreamService],
    ) -> StreamDetail:
        return await jetstream.create_stream(server_id, data)

    @get("/streams/{name:str}")
    async def get_stream(
        self,
        server_id: FromPath[uuid.UUID],
        name: FromPath[str],
        jetstream: NamedDependency[JetStreamService],
    ) -> StreamDetail:
        return await jetstream.get_stream(server_id, name)

    @patch("/streams/{name:str}", summary="Change a live stream, keeping its messages")
    async def update_stream(
        self,
        server_id: FromPath[uuid.UUID],
        name: FromPath[str],
        data: StreamUpdate,
        jetstream: NamedDependency[JetStreamService],
    ) -> StreamDetail:
        return await jetstream.update_stream(server_id, name, data)

    @delete("/streams/{name:str}")
    async def delete_stream(
        self,
        server_id: FromPath[uuid.UUID],
        name: FromPath[str],
        jetstream: NamedDependency[JetStreamService],
    ) -> None:
        await jetstream.delete_stream(server_id, name)

    @post("/streams/{name:str}/purge")
    async def purge_stream(
        self,
        server_id: FromPath[uuid.UUID],
        name: FromPath[str],
        data: PurgeRequest,
        jetstream: NamedDependency[JetStreamService],
    ) -> StreamDetail:
        return await jetstream.purge_stream(server_id, name, data)

    @get(
        "/streams/{name:str}/subjects",
        summary="Server-side per-subject counts via stream_info(subjects_filter=...)",
    )
    async def stream_subjects(
        self,
        server_id: FromPath[uuid.UUID],
        name: FromPath[str],
        jetstream: NamedDependency[JetStreamService],
        filter: FromQuery[str] = ">",
    ) -> list[SubjectCount]:
        return await jetstream.stream_subjects(server_id, name, filter)

    @get("/streams/{name:str}/consumers")
    async def list_consumers(
        self,
        server_id: FromPath[uuid.UUID],
        name: FromPath[str],
        jetstream: NamedDependency[JetStreamService],
    ) -> list[ConsumerSummary]:
        return await jetstream.list_consumers(server_id, name)

    @post("/streams/{name:str}/consumers", status_code=201)
    async def create_consumer(
        self,
        server_id: FromPath[uuid.UUID],
        name: FromPath[str],
        data: ConsumerCreate,
        jetstream: NamedDependency[JetStreamService],
    ) -> ConsumerSummary:
        return await jetstream.create_consumer(server_id, name, data)

    @get("/streams/{name:str}/consumers/{consumer:str}")
    async def get_consumer(
        self,
        server_id: FromPath[uuid.UUID],
        name: FromPath[str],
        consumer: FromPath[str],
        jetstream: NamedDependency[JetStreamService],
    ) -> ConsumerSummary:
        return await jetstream.get_consumer(server_id, name, consumer)

    @patch("/streams/{name:str}/consumers/{consumer:str}", summary="Change a running consumer")
    async def update_consumer(
        self,
        server_id: FromPath[uuid.UUID],
        name: FromPath[str],
        consumer: FromPath[str],
        data: ConsumerUpdate,
        jetstream: NamedDependency[JetStreamService],
    ) -> ConsumerSummary:
        return await jetstream.update_consumer(server_id, name, consumer, data)

    @post(
        "/streams/{name:str}/consumers/{consumer:str}/pause",
        summary="Stop delivery without losing the consumer's position",
    )
    async def pause_consumer(
        self,
        server_id: FromPath[uuid.UUID],
        name: FromPath[str],
        consumer: FromPath[str],
        data: ConsumerPauseRequest,
        jetstream: NamedDependency[JetStreamService],
    ) -> ConsumerSummary:
        return await jetstream.pause_consumer(server_id, name, consumer, data)

    @post("/streams/{name:str}/consumers/{consumer:str}/resume")
    async def resume_consumer(
        self,
        server_id: FromPath[uuid.UUID],
        name: FromPath[str],
        consumer: FromPath[str],
        jetstream: NamedDependency[JetStreamService],
    ) -> ConsumerSummary:
        return await jetstream.resume_consumer(server_id, name, consumer)

    @delete("/streams/{name:str}/consumers/{consumer:str}")
    async def delete_consumer(
        self,
        server_id: FromPath[uuid.UUID],
        name: FromPath[str],
        consumer: FromPath[str],
        jetstream: NamedDependency[JetStreamService],
    ) -> None:
        await jetstream.delete_consumer(server_id, name, consumer)

    @post("/streams/{name:str}/messages", summary="Read stored messages by seq or subject")
    async def read_messages(
        self,
        server_id: FromPath[uuid.UUID],
        name: FromPath[str],
        data: MessageQuery,
        jetstream: NamedDependency[JetStreamService],
    ) -> list[StoredMessage]:
        return await jetstream.read_messages(server_id, name, data)

    @delete("/streams/{name:str}/messages/{seq:int}")
    async def delete_message(
        self,
        server_id: FromPath[uuid.UUID],
        name: FromPath[str],
        seq: FromPath[int],
        jetstream: NamedDependency[JetStreamService],
    ) -> None:
        await jetstream.delete_message(server_id, name, seq)
