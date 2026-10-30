"""Database engine, table creation, and the FastAPI session dependency."""

from pathlib import Path
from typing import Annotated as A, AsyncIterator, Literal

from fastapi import Depends, Request
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from .schemas import CollabTable


def make_engine(
    database_url: str, journal_mode: Literal["WAL", "DELETE"] = "WAL"
) -> AsyncEngine:
    """
    The engine for `database_url`. On SQLite each connection gets
    `journal_mode` (WAL lets readers proceed beside the one writer), a busy
    timeout (a second publisher waits for the first rather than failing with
    "database is locked"), and foreign keys.
    """
    engine = create_async_engine(database_url)
    if database_url.startswith("sqlite"):
        Path(database_url.split("///", 1)[-1]).parent.mkdir(parents=True, exist_ok=True)

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(f"PRAGMA journal_mode={journal_mode}")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

    return engine


async def init_db(engine: AsyncEngine) -> None:
    """Create this server's tables (and only those). Run once at startup."""
    async with engine.begin() as conn:
        await conn.run_sync(CollabTable.metadata.create_all)


async def _get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """
    A session on this app's engine. Services commit explicitly: publishing a
    version is one transaction they own, not one a request wraps.
    """
    async with AsyncSession(request.app.state.engine) as session:
        yield session


SessionDep = A[AsyncSession, Depends(_get_session)]
