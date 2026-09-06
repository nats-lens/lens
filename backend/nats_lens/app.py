"""The Litestar application.

Wave 0 wires every route with its real path, parameters and return type so the
OpenAPI schema is complete and the frontend client generates on day one. The
handler bodies land in later waves; each controller file has exactly one owner.

Two long-lived resources are owned by lifespan context managers rather than
startup hooks, because both need paired teardown: the SQLAlchemy engine, and the
NATS ConnectionManager (which must `drain()` before it closes).
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from mimetypes import guess_type

from litestar import Litestar, MediaType, Response, get
from litestar.channels import ChannelsPlugin
from litestar.channels.backends.memory import MemoryChannelsBackend
from litestar.config.cors import CORSConfig
from litestar.di import Provide
from litestar.openapi import OpenAPIConfig
from litestar.params import FromPath

from nats_lens import __version__
from nats_lens.config import Settings
from nats_lens.conn.manager import ConnectionManager, provide_connections
from nats_lens.db.session import make_engine, make_session_factory, provide_session
from nats_lens.domain.advisories.controller import AdvisoriesController
from nats_lens.domain.common import AppHealth, HealthStatus
from nats_lens.domain.core.controller import CoreController
from nats_lens.domain.jetstream.controller import JetStreamController
from nats_lens.domain.kv.controller import KeyValueController
from nats_lens.domain.monitor.controller import MonitorController
from nats_lens.domain.objects.controller import ObjectStoreController
from nats_lens.domain.protoschemas.controller import SchemasController
from nats_lens.domain.protoschemas.repository import SchemaRepository
from nats_lens.domain.protoschemas.service import SchemaService
from nats_lens.domain.protoschemas.store import ProtoStore
from nats_lens.domain.servers.controller import ServersController
from nats_lens.domain.servers.repository import SecretVault, SqlRegistry
from nats_lens.domain.ws_controller import core_websocket

logger = logging.getLogger("nats_lens.app")

ASSETS_PREFIX = "assets/"
"""Where Vite writes its content-hashed bundles. Only these are safe to cache forever."""

CHANNELS = ["servers"]
"""Static channels. Per-server channels (`core:<id>:<sub>`) are created on demand."""


@get("/api/health", tags=["health"], summary="Liveness, and what is connected")
async def health() -> AppHealth:
    return AppHealth(
        status=HealthStatus.OK,
        version=__version__,
        database=True,
        servers_registered=0,
        servers_connected=0,
    )


def _assert_single_worker() -> None:
    """All NATS state lives in this process.

    Subscriptions, the capture rings and the monitoring pollers are in-memory, so
    a second worker would serve a different, silently inconsistent view. Fail loudly
    rather than confusingly.
    """
    workers = os.environ.get("WEB_CONCURRENCY")
    if workers not in (None, "", "1"):
        raise RuntimeError(
            f"nats-lens holds its NATS connections in-process and must run with a single "
            f"worker, but WEB_CONCURRENCY={workers}. Run granian with --workers 1."
        )


def create_app(settings: Settings | None = None) -> Litestar:
    settings = settings or Settings.from_env()
    _assert_single_worker()

    @asynccontextmanager
    async def db_lifespan(app: Litestar) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url, echo=settings.debug)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        try:
            yield
        finally:
            await engine.dispose()

    @asynccontextmanager
    async def schema_lifespan(app: Litestar) -> AsyncIterator[None]:
        """Proto definitions, from both sources.

        Scanned once at boot so a mounted directory is live the moment the app
        is, rather than after someone finds the rescan button. A failure here is
        logged and swallowed: a malformed .proto in a mounted tree is the
        operator's to fix, and refusing to start over it would take the whole
        tool down for one bad file.
        """
        store = ProtoStore(settings.proto_upload_dir, settings.proto_mount_dir)
        app.state.proto_store = store
        session_factory = app.state.session_factory
        try:
            async with session_factory() as session:
                report = await SchemaService(SchemaRepository(session), store=store).scan_sources()
                await session.commit()
            registered = sum(1 for e in report.entries if e.status == "registered")
            failed = [e for e in report.entries if e.status == "failed"]
            logger.info(
                "proto scan: %d registered, %d unchanged, %d failed (uploads=%s mounted=%s)",
                registered,
                sum(1 for e in report.entries if e.status == "unchanged"),
                len(failed),
                store.upload_dir,
                store.mount_dir or "not configured",
            )
            for entry in failed:
                logger.warning("proto scan: %s could not be read -- %s", entry.path, entry.detail)
        except Exception:
            logger.exception("proto scan failed; definitions can still be uploaded")
        yield

    @asynccontextmanager
    async def nats_lifespan(app: Litestar) -> AsyncIterator[None]:
        """The NATS connections, for the life of the process.

        Ordered after `db_lifespan` because the registry reads its specs from the
        session factory that one creates. `start()` opens only the servers marked
        `connect_on_startup`, concurrently and with each failure absorbed, so a NATS
        server that is down cannot keep nats-lens from booting.
        """
        registry = SqlRegistry(app.state.session_factory, SecretVault(settings.secret_key))
        manager = ConnectionManager(registry, settings=settings)
        app.state.connections = manager
        try:
            await manager.start()
            yield
        finally:
            await manager.stop()

    routers = [
        health,
        ServersController,
        CoreController,
        JetStreamController,
        KeyValueController,
        ObjectStoreController,
        SchemasController,
        MonitorController,
        AdvisoriesController,
        core_websocket,
    ]

    # In the production image the SPA is copied in and served by this process.
    # In dev the directory does not exist and Vite serves it instead, so the
    # catch-all is simply not registered and /api keeps its 404s honest.
    if settings.static_dir is not None and settings.static_dir.is_dir():
        static_dir = settings.static_dir.resolve()
        index_html = (static_dir / "index.html").read_bytes()

        # Two paths, one handler: `/{spa_path:path}` does not match the bare root,
        # so "/" is listed explicitly rather than left to 404.
        @get(["/", "/{spa_path:path}"], include_in_schema=False, name="spa")
        async def spa(spa_path: FromPath[str] = "") -> Response[bytes]:
            """Serve the built SPA: real files as themselves, everything else as index.

            One handler rather than a static router plus a fallback. A catch-all
            `/{path:path}` outranks Litestar's static router, so the two together
            served index.html for `/assets/*.js` as well -- a blank page whose only
            symptom is that the bundle is 407 bytes of HTML. Doing both jobs here
            removes the ordering question entirely.

            Registered last, so /api, /ws and /schema are matched before it.
            """
            candidate = (static_dir / spa_path.lstrip("/")).resolve()
            # A client-side route is not a file, and `..` must not escape the root.
            if spa_path and candidate.is_file() and candidate.is_relative_to(static_dir):
                # Only Vite's own output carries a content hash in its name, so
                # only it can be cached immutably. Everything else under the root
                # -- favicons, the manifest -- keeps its filename forever, and a
                # year-long immutable cache would mean a replaced icon never
                # reaches anyone who had already loaded the old one.
                fingerprinted = spa_path.lstrip("/").startswith(ASSETS_PREFIX)
                cache = (
                    "public, max-age=31536000, immutable"
                    if fingerprinted
                    else "public, max-age=3600"
                )
                return Response(
                    candidate.read_bytes(),
                    media_type=guess_type(candidate.name)[0] or "application/octet-stream",
                    headers={"cache-control": cache},
                )
            return Response(
                index_html,
                media_type=MediaType.HTML,
                headers={"cache-control": "no-cache"},
            )

        routers.append(spa)

    return Litestar(
        route_handlers=routers,
        plugins=[
            ChannelsPlugin(
                backend=MemoryChannelsBackend(history=0),
                channels=CHANNELS,
                arbitrary_channels_allowed=True,
                subscriber_max_backlog=settings.ws_queue_size,
                subscriber_backlog_strategy="dropleft",
            )
        ],
        dependencies={
            "session": Provide(provide_session),
            "connections": Provide(provide_connections, sync_to_thread=False),
        },
        lifespan=[db_lifespan, schema_lifespan, nats_lifespan],
        cors_config=CORSConfig(allow_origins=list(settings.cors_origins))
        if settings.cors_origins
        else None,
        openapi_config=OpenAPIConfig(
            title="nats-lens",
            version=__version__,
            description=(
                "A NATS management GUI that says where every number came from. "
                "Values arrive wrapped in `Sourced`, carrying either a value and its "
                "source, or no value and the reason plus the fix -- never a zero "
                "standing in for something the backend could not see."
            ),
            path="/schema",
        ),
        debug=settings.debug,
    )


app = create_app()
