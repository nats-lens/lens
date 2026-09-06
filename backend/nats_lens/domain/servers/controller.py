"""Servers screen + Add a server. OWNER: agent B1-connections."""

from __future__ import annotations

import uuid

from litestar import Controller, delete, get, patch, post
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath

from nats_lens.domain.servers.schemas import (
    ProbeRequest,
    ProbeResponse,
    ServerConfig,
    ServerCreate,
    ServerDetail,
    ServerSummary,
    ServerUpdate,
)
from nats_lens.domain.servers.service import ServerService, provide_server_service


class ServersController(Controller):
    path = "/api/servers"
    tags = ["servers"]
    dependencies = {"servers": Provide(provide_server_service, sync_to_thread=False)}

    @get("/", summary="Every registered server, with live state and provenance")
    async def list_servers(self, servers: NamedDependency[ServerService]) -> list[ServerSummary]:
        return await servers.list_summaries()

    @post("/", status_code=201, summary="Register a server")
    async def create_server(
        self, servers: NamedDependency[ServerService], data: ServerCreate
    ) -> ServerConfig:
        return await servers.create(data)

    @post("/probe", summary="Probe a client endpoint and a monitoring URL independently")
    async def probe(
        self, servers: NamedDependency[ServerService], data: ProbeRequest
    ) -> ProbeResponse:
        return await servers.probe(data)

    @get("/{server_id:uuid}", summary="The Servers screen detail panel")
    async def get_server(
        self, servers: NamedDependency[ServerService], server_id: FromPath[uuid.UUID]
    ) -> ServerDetail:
        return await servers.detail(server_id)

    @get("/{server_id:uuid}/config", summary="The saved form. Secrets appear only as refs")
    async def get_config(
        self, servers: NamedDependency[ServerService], server_id: FromPath[uuid.UUID]
    ) -> ServerConfig:
        return await servers.config(server_id)

    @patch("/{server_id:uuid}", summary="Update a server")
    async def update_server(
        self,
        servers: NamedDependency[ServerService],
        server_id: FromPath[uuid.UUID],
        data: ServerUpdate,
    ) -> ServerConfig:
        return await servers.update(server_id, data)

    @delete("/{server_id:uuid}", summary="Forget a server and its credentials")
    async def delete_server(
        self, servers: NamedDependency[ServerService], server_id: FromPath[uuid.UUID]
    ) -> None:
        await servers.delete(server_id)

    @post("/{server_id:uuid}/connect", summary="Open the connection now")
    async def connect(
        self, servers: NamedDependency[ServerService], server_id: FromPath[uuid.UUID]
    ) -> ServerDetail:
        return await servers.connect(server_id)

    @post("/{server_id:uuid}/disconnect", summary="Drain and close")
    async def disconnect(
        self, servers: NamedDependency[ServerService], server_id: FromPath[uuid.UUID]
    ) -> ServerDetail:
        return await servers.disconnect(server_id)
