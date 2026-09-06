"""Core NATS: subscribe, publish, request. OWNER: agent B5-messaging.

HTTP creates subscriptions and performs every mutation; the websocket
(`domain/ws_controller.py`) only ever joins a channel this controller hands
back. That split is what makes the socket idempotent and safe to reconnect
blindly -- replaying a join re-runs nothing.
"""

from __future__ import annotations

import uuid

from litestar import Controller, delete, get, post
from litestar.channels import ChannelsPlugin
from litestar.datastructures import State
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath
from sqlalchemy.ext.asyncio import AsyncSession

from nats_lens.conn.manager import ConnectionManager
from nats_lens.domain.core.schemas import (
    CapturedMessage,
    PublishRequest,
    PublishResult,
    RequestRequest,
    RequestResult,
    SubjectChip,
    SubscriptionCreate,
    SubscriptionInfo,
)
from nats_lens.domain.core.service import CoreService


async def provide_core(
    session: NamedDependency[AsyncSession],
    connections: NamedDependency[ConnectionManager],
    channels: NamedDependency[ChannelsPlugin],
    state: State,
) -> CoreService:
    return CoreService(session, connections, channels, state.session_factory)


class CoreController(Controller):
    path = "/api/servers/{server_id:uuid}/core"
    tags = ["core"]
    dependencies = {"core": Provide(provide_core)}

    @get("/subscriptions")
    async def list_subscriptions(
        self, server_id: FromPath[uuid.UUID], core: NamedDependency[CoreService]
    ) -> list[SubscriptionInfo]:
        return await core.list_subscriptions(server_id)

    @post(
        "/subscriptions",
        status_code=201,
        summary="Create a subscription and get back the channel the websocket joins",
    )
    async def subscribe(
        self,
        server_id: FromPath[uuid.UUID],
        data: SubscriptionCreate,
        core: NamedDependency[CoreService],
    ) -> SubscriptionInfo:
        return await core.subscribe(server_id, data)

    @delete("/subscriptions/{sub_id:uuid}")
    async def unsubscribe(
        self,
        server_id: FromPath[uuid.UUID],
        sub_id: FromPath[uuid.UUID],
        core: NamedDependency[CoreService],
    ) -> None:
        await core.unsubscribe(server_id, sub_id)

    @post("/publish")
    async def publish(
        self,
        server_id: FromPath[uuid.UUID],
        data: PublishRequest,
        core: NamedDependency[CoreService],
    ) -> PublishResult:
        return await core.publish(server_id, data)

    @post("/request", summary="Request-reply, including NoRespondersError as a result")
    async def request(
        self,
        server_id: FromPath[uuid.UUID],
        data: RequestRequest,
        core: NamedDependency[CoreService],
    ) -> RequestResult:
        return await core.request(server_id, data)

    @get(
        "/messages/{capture_id:str}",
        summary="The full message behind a transcript row, with every inspector view",
    )
    async def get_message(
        self,
        server_id: FromPath[uuid.UUID],
        capture_id: FromPath[str],
        core: NamedDependency[CoreService],
    ) -> CapturedMessage:
        return await core.get_message(server_id, capture_id)

    @get("/chips", summary="Saved subject filters with what nats-lens has actually seen")
    async def chips(
        self, server_id: FromPath[uuid.UUID], core: NamedDependency[CoreService]
    ) -> list[SubjectChip]:
        return await core.chips(server_id)
