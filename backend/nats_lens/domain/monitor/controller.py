"""The HTTP monitoring port. OWNER: agent B3-monitoring.

Thin by design. Every decision worth arguing about -- what counts as reachable,
what a missing port produces, which health probes to run -- lives in service.py
where it can be tested without a request.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from litestar import Controller, get
from litestar.di import NamedDependency, Provide
from litestar.params import FromPath, FromQuery, QueryParameter
from sqlalchemy.ext.asyncio import AsyncSession

from nats_lens.domain.monitor.schemas import (
    ConnzPage,
    ConnzQuery,
    HealthCheck,
    HealthQuery,
    MonitorOverview,
    PrometheusHint,
    RoutezSummary,
    SubszQuery,
    SubszSummary,
)
from nats_lens.domain.monitor.service import MonitorService


async def provide_monitor(session: NamedDependency[AsyncSession]) -> MonitorService:
    return MonitorService(session)


class MonitorController(Controller):
    path = "/api/servers/{server_id:uuid}/monitor"
    tags = ["monitor"]
    dependencies = {"monitor": Provide(provide_monitor)}

    @get("/", summary="/varz and /jsz, with rates sampled between the last two polls")
    async def overview(
        self, server_id: FromPath[uuid.UUID], monitor: NamedDependency[MonitorService]
    ) -> MonitorOverview:
        return await monitor.overview(server_id)

    @get("/connections", summary="/connz with its real sort, limit, offset and subs surface")
    async def connections(
        self,
        server_id: FromPath[uuid.UUID],
        monitor: NamedDependency[MonitorService],
        sort: FromQuery[str] = "cid",
        limit: FromQuery[int] = 100,
        offset: FromQuery[int] = 0,
        subs: FromQuery[bool] = False,
        auth: FromQuery[bool] = False,
        account: FromQuery[str | None] = None,
        # `state` is a Litestar reserved kwarg (app state), so the Python name
        # differs from the query name /connz actually expects.
        conn_state: Annotated[str, QueryParameter(name="state")] = "open",
    ) -> ConnzPage:
        """Parameters are spelled out rather than taken as one struct.

        Litestar binds a msgspec struct from the query string happily enough, but
        it does not describe one in the OpenAPI schema -- so the generated
        TypeScript client would not know these exist, and the screen's sort and
        paging controls would be untyped strings. The struct is still what the
        service takes; it is just assembled here.
        """
        return await monitor.connections(
            server_id,
            ConnzQuery(
                sort=sort,
                limit=limit,
                offset=offset,
                subs=subs,
                auth=auth,
                account=account,
                state=conn_state,
            ),
        )

    @get(
        "/subscriptions",
        summary="/subsz -- who is listening, and whether a subject reaches anyone",
    )
    async def subscriptions(
        self,
        server_id: FromPath[uuid.UUID],
        monitor: NamedDependency[MonitorService],
        subs: FromQuery[bool] = True,
        offset: FromQuery[int] = 0,
        limit: FromQuery[int] = 100,
        account: FromQuery[str | None] = None,
        test: FromQuery[str | None] = None,
    ) -> SubszSummary:
        """`test` is the interesting parameter.

        Given a concrete subject it reports which subscriptions would match it,
        which is the only way to answer "why is nobody receiving this?" -- the
        client protocol cannot see another connection's interest.
        """
        return await monitor.subscriptions(
            server_id,
            SubszQuery(subs=subs, offset=offset, limit=limit, account=account, test=test),
        )

    @get("/routes", summary="/routez, plus gateways and leafnodes")
    async def routes(
        self, server_id: FromPath[uuid.UUID], monitor: NamedDependency[MonitorService]
    ) -> RoutezSummary:
        return await monitor.routes(server_id)

    @get("/health", summary="/healthz variants. A 503 is a result, not an error")
    async def health(
        self,
        server_id: FromPath[uuid.UUID],
        monitor: NamedDependency[MonitorService],
        js_enabled_only: FromQuery[bool] = False,
        js_server_only: FromQuery[bool] = False,
        stream: FromQuery[str | None] = None,
        consumer: FromQuery[str | None] = None,
        account: FromQuery[str | None] = None,
    ) -> list[HealthCheck]:
        """Spelled out for the same reason as `connections` above."""
        return await monitor.health(
            server_id,
            HealthQuery(
                js_enabled_only=js_enabled_only,
                js_server_only=js_server_only,
                stream=stream,
                consumer=consumer,
                account=account,
            ),
        )

    @get("/prometheus", summary="Where to go for history, since nats-lens keeps none")
    async def prometheus(
        self, server_id: FromPath[uuid.UUID], monitor: NamedDependency[MonitorService]
    ) -> PrometheusHint:
        return await monitor.prometheus(server_id)
