"""JetStream advisories and $SYS events. OWNER: agent B5-messaging.

Thin by design -- see domain/monitor/controller.py.

Reading any of these routes starts the feed for that server if it is not already
running, which is why every one of them can take a moment on first call. Nothing
is stored: advisories are published once and never kept, so the feed only ever
reports what it has seen since it started.
"""

from __future__ import annotations

import uuid

from litestar import Controller, get, post
from litestar.channels import ChannelsPlugin
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath, FromQuery

from nats_lens.conn.manager import ConnectionManager
from nats_lens.domain.advisories.schemas import (
    AdvisoryEvent,
    AdvisoryFeedState,
    AdvisoryKind,
    AdvisoryTypeCount,
    CaptureStreamRequest,
)
from nats_lens.domain.advisories.service import AdvisoriesService


async def provide_advisories(
    connections: NamedDependency[ConnectionManager], channels: NamedDependency[ChannelsPlugin]
) -> AdvisoriesService:
    return AdvisoriesService(connections, channels)


class AdvisoriesController(Controller):
    path = "/api/servers/{server_id:uuid}/advisories"
    tags = ["advisories"]
    dependencies = {"advisories": Provide(provide_advisories)}

    @get("/", summary="What this feed has seen since it started. Never a server-side total")
    async def list_events(
        self,
        server_id: FromPath[uuid.UUID],
        advisories: NamedDependency[AdvisoriesService],
        kind: FromQuery[AdvisoryKind | None] = None,
        limit: FromQuery[int] = 200,
    ) -> list[AdvisoryEvent]:
        return await advisories.list_events(server_id, kind, limit)

    @get("/state", summary="Whether anything is being kept. By default, nothing is")
    async def state(
        self, server_id: FromPath[uuid.UUID], advisories: NamedDependency[AdvisoriesService]
    ) -> AdvisoryFeedState:
        return await advisories.state(server_id)

    @get("/counts")
    async def counts(
        self, server_id: FromPath[uuid.UUID], advisories: NamedDependency[AdvisoriesService]
    ) -> list[AdvisoryTypeCount]:
        return await advisories.counts(server_id)

    @post("/capture", summary="Make advisories durable by having JetStream keep them")
    async def create_capture(
        self,
        server_id: FromPath[uuid.UUID],
        data: CaptureStreamRequest,
        advisories: NamedDependency[AdvisoriesService],
    ) -> AdvisoryFeedState:
        return await advisories.create_capture(server_id, data)
