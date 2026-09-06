"""Async engine and the per-request session dependency.

SQLite, not a server. The registry is a handful of small tables written by a
single process, so a file is the right shape -- and it means neither the dev
environment nor a deployment needs a database container.

Two pragmas are set on every connection, because SQLite's defaults are wrong for
this use:

  foreign_keys=ON   off by default, and the schema leans on ON DELETE CASCADE
  journal_mode=WAL  lets the reads that render a screen run while a write commits
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from litestar.datastructures import State
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    engine = create_async_engine(database_url, echo=echo)

    if database_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            # Wait rather than fail if a read is in flight when we write.
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def provide_session(state: State) -> AsyncIterator[AsyncSession]:
    """Litestar dependency: one transaction per request, committed on a clean exit."""
    factory: async_sessionmaker[AsyncSession] = state.session_factory
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()
