"""Every NATS connection nats-lens holds, in one place.

Created once in Litestar's lifespan and injected into the routes that need it. It
is the reason `app.py` refuses to run with more than one worker: subscriptions,
JetStream contexts and the monitoring pollers all live in this object, and a second
worker would serve a second, silently different view of the same servers.

Connections are opened lazily. `connect_on_startup` is the only exception, and even
then the servers are opened concurrently behind a short timeout so one dead host
delays nothing and blocks nobody.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator, Mapping
from typing import Protocol

from litestar.datastructures import State

from nats_lens.config import Settings
from nats_lens.conn.connection import (
    ConnectionSpec,
    Connector,
    ManagedConnection,
    MonitorProbe,
    default_connector,
    healthz,
)
from nats_lens.domain.servers.schemas import ConnectionState

_SHUTDOWN_GRACE = 5.0
"""How long a drain gets before shutdown stops waiting for it."""


class UnknownServer(LookupError):
    """No server with that id is registered."""

    def __init__(self, server_id: uuid.UUID) -> None:
        self.server_id = server_id
        super().__init__(f"No server is registered with id {server_id}.")


class Registry(Protocol):
    """Where connection specs come from.

    A protocol rather than an import: the manager must not depend on SQLAlchemy or
    on the servers domain, and the unit suite supplies specs from a list.
    """

    async def load(self, server_id: uuid.UUID) -> ConnectionSpec | None: ...

    async def load_startup(self) -> list[ConnectionSpec]: ...


class ConnectionManager:
    """Holds one `ManagedConnection` per registered server, opened on demand."""

    def __init__(
        self,
        registry: Registry,
        *,
        settings: Settings,
        connector: Connector | None = None,
        monitor_probe: MonitorProbe = healthz,
    ) -> None:
        self.settings = settings
        self.monitor_probe = monitor_probe
        self._registry = registry
        self._connector = connector
        self._connections: dict[uuid.UUID, ManagedConnection] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        """Open the servers marked `connect_on_startup`, and only those.

        Concurrently, and with each failure absorbed: booting must not depend on a
        NATS server being up, so a host that is down is recorded as `last_error` on
        its own card and the rest carry on.
        """
        specs = await self._registry.load_startup()
        if not specs:
            return
        async with asyncio.TaskGroup() as group:
            for spec in specs:
                group.create_task(self._open_quietly(spec))

    async def _open_quietly(self, spec: ConnectionSpec) -> None:
        conn = self._track(spec)
        budget = self.settings.connect_timeout_seconds + 1.0
        try:
            async with asyncio.timeout(budget):
                await conn.open()
        except Exception as exc:
            # Deliberately swallowed. A TaskGroup cancels its siblings on the first
            # escaping exception, which would turn one unreachable server into a
            # failed startup for all of them.
            conn.record_error(exc)

    async def stop(self) -> None:
        """Drain and close everything. Best effort, bounded."""
        connections = list(self._connections.values())
        self._connections.clear()
        if not connections:
            return
        try:
            async with asyncio.timeout(_SHUTDOWN_GRACE):
                async with asyncio.TaskGroup() as group:
                    for conn in connections:
                        group.create_task(conn.close())
        except TimeoutError, ExceptionGroup:
            pass

    # ---------------------------------------------------------------- access

    @property
    def connector(self) -> Connector:
        """What actually opens sockets. The probe endpoint borrows it, so a test
        that swaps the connector swaps it everywhere at once."""
        return self._connector or default_connector()

    def peek(self, server_id: uuid.UUID) -> ManagedConnection | None:
        """What is known about a server right now, without opening anything."""
        return self._connections.get(server_id)

    def __iter__(self) -> Iterator[ManagedConnection]:
        return iter(list(self._connections.values()))

    @property
    def tracked(self) -> Mapping[uuid.UUID, ManagedConnection]:
        return self._connections

    @property
    def connected_count(self) -> int:
        return sum(1 for c in self._connections.values() if c.is_connected)

    def _track(self, spec: ConnectionSpec) -> ManagedConnection:
        existing = self._connections.get(spec.server_id)
        if existing is not None and existing.spec == spec:
            return existing
        if existing is not None:
            existing.spec = spec
            return existing
        conn = ManagedConnection(
            spec,
            connect_timeout=self.settings.connect_timeout_seconds,
            monitor_timeout=self.settings.monitor_timeout_seconds,
            connector=self._connector,
            monitor_probe=self.monitor_probe,
        )
        self._connections[spec.server_id] = conn
        return conn

    async def _spec(self, server_id: uuid.UUID) -> ConnectionSpec:
        spec = await self._registry.load(server_id)
        if spec is None:
            raise UnknownServer(server_id)
        return spec

    # --------------------------------------------------------------- opening

    async def ensure(self, server_id: uuid.UUID) -> ManagedConnection:
        """The lazy path every other domain uses. Connects on first use.

        Raises whatever nats-py raised, so a caller can turn it straight into a
        problem detail with `NatsProblem.of`.
        """
        conn = self.peek(server_id)
        if conn is not None and conn.is_connected:
            return conn
        return await self.connect(server_id)

    async def connect(self, server_id: uuid.UUID) -> ManagedConnection:
        """Open now, re-reading the saved configuration first."""
        async with self._lock:
            conn = self._track(await self._spec(server_id))
        await conn.open()
        return conn

    async def disconnect(self, server_id: uuid.UUID) -> ManagedConnection | None:
        conn = self.peek(server_id)
        if conn is not None:
            await conn.close()
        return conn

    async def reload(self, server_id: uuid.UUID) -> ManagedConnection | None:
        """Pick up an edited configuration.

        A live connection is closed and reopened, because the fields that changed
        are the ones the socket was built from. A connection that was not open stays
        closed -- editing a server is not a reason to dial it.
        """
        conn = self.peek(server_id)
        if conn is None:
            return None
        was_connected = conn.is_connected
        await conn.close()
        spec = await self._registry.load(server_id)
        if spec is None:
            self._connections.pop(server_id, None)
            return None
        conn.spec = spec
        if was_connected:
            try:
                await conn.open()
            except Exception as exc:
                conn.record_error(exc)
        return conn

    async def forget(self, server_id: uuid.UUID) -> None:
        """Close and drop, for a server that no longer exists."""
        conn = self._connections.pop(server_id, None)
        if conn is not None:
            await conn.close()

    def state_of(self, server_id: uuid.UUID) -> ConnectionState:
        conn = self.peek(server_id)
        return conn.state if conn is not None else ConnectionState.DISCONNECTED


def provide_connections(state: State) -> ConnectionManager:
    """Litestar dependency. The manager is built once, in the lifespan."""
    manager: ConnectionManager = state.connections
    return manager
