"""
Database engine, session factory, and FastAPI session dependency.
"""

import functools
from pathlib import Path
from typing import Annotated as A, AsyncIterator
from fastapi import Depends
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from ..config import settings
from .seed import seed_db


@functools.cache
def get_engine() -> AsyncEngine:
    """
    Build the AsyncEngine. For SQLite, attach a connect-time listener
    that runs PRAGMAs on each new connection:

      - `journal_mode=WAL`: WAL lets concurrent readers proceed
        alongside a single writer, instead of the default rollback
        journal where readers and writers block each other.
      - `busy_timeout=30000`: when a writer is held off (one writer
        max even in WAL), retry-wait up to 30 seconds before raising
        "database is locked". The default of 5s is too tight for our
        background indexer, which can take tens of seconds between
        opening a session and committing.
      - `synchronous=NORMAL`: a slight durability/perf trade-off that
        pairs well with WAL — fsync only at WAL checkpoints, not on
        every commit. We're an app's metadata DB, not an OLTP store
        where every transaction must survive power loss; this is the
        recommended setting for WAL.

    These help with the GET-vs-background-indexer race in the
    knowledge_bases router. Without them, an in-flight indexer
    commit can stall a concurrent GET (or vice versa) long enough
    to hit the 5s default timeout and 500 the request.

    Pool settings: SQLAlchemy's defaults (pool_size=5, max_overflow=10,
    pool_timeout=30s) plus a small app are usually fine, but our pattern
    of multiple short-lived background sessions (indexer persist,
    reconciler persist, KB delete) plus request sessions can pile up
    when commits are retry-blocked. We raise both pool ceilings and
    drop pool_timeout so a temporarily-exhausted pool fails the GET
    quickly (returning 5xx for the client to retry) instead of hanging
    other requests behind a 30-second QueuePool wait. We also recycle
    idle connections after 1 hour as a defense against macOS aggressive
    fd reaping on long-idle processes.
    """
    pool_kwargs: dict = {}
    if settings.database_url.startswith("sqlite"):
        pool_kwargs = {
            "pool_size": 10,
            "max_overflow": 20,
            "pool_timeout": 10.0,
            "pool_recycle": 3600,
            "pool_pre_ping": True,
        }
    engine = create_async_engine(settings.database_url, **pool_kwargs)

    if settings.database_url.startswith("sqlite"):
        # The event must hook the sync engine; the async wrapper
        # exposes `engine.sync_engine` for exactly this case.
        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA synchronous=NORMAL")
                # Required for ON DELETE CASCADE; SQLite leaves FK enforcement off by default.
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

    return engine


async def init_db() -> None:
    """Create tables and seed defaults. Call once at app startup."""
    if settings.database_url.startswith("sqlite"):
        Path(settings.database_url.split("///", 1)[-1]).parent.mkdir(
            parents=True, exist_ok=True
        )
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    await seed_db(engine)


EngineDep = A[AsyncEngine, Depends(get_engine)]


# How long writes wait, in total, for the writer lock to free up during
# commit before giving up. SQLite is single-writer; the busy_timeout
# PRAGMA (30s) handles brief contention at the driver level, but bursts
# of concurrent writers (e.g. a UI poll's reconciler hits at the same
# instant a POST commits) can occasionally slip past that. Retry here
# is the second line of defense.
_COMMIT_RETRY_ATTEMPTS = 5
_COMMIT_RETRY_INITIAL = 0.2
_COMMIT_RETRY_MAX = 2.0


def _is_locked_error(exc: BaseException) -> bool:
    """True iff the OperationalError is the sqlite 'database is locked' case."""
    from sqlalchemy.exc import OperationalError

    return (
        isinstance(exc, OperationalError) and "database is locked" in str(exc).lower()
    )


async def commit_with_retry(session: AsyncSession, *, context: str = "") -> bool:
    """
    Commit `session`, retrying on `OperationalError: database is locked`
    with exponential backoff. Returns True on success, False if every
    attempt timed out. Non-lock errors are re-raised after rolling back.

    On lock-timeout the session is rolled back so the caller's identity
    map is clean. Callers that *must* land the write (user-initiated
    POST/PUT/DELETE) should treat False as a 5xx; callers that can drop
    the write (idempotent reconciler bookkeeping) should ignore False
    and rely on the next round to retry.
    """
    import asyncio
    import logging
    from sqlalchemy.exc import OperationalError

    logger = logging.getLogger("vista.db")
    delay = _COMMIT_RETRY_INITIAL
    for attempt in range(1, _COMMIT_RETRY_ATTEMPTS + 1):
        try:
            await session.commit()
            return True
        except OperationalError as exc:
            if not _is_locked_error(exc):
                await session.rollback()
                raise
            await session.rollback()
            if attempt == _COMMIT_RETRY_ATTEMPTS:
                logger.warning(
                    "Commit deferred after %d attempts%s: database is locked.",
                    _COMMIT_RETRY_ATTEMPTS,
                    f" ({context})" if context else "",
                )
                return False
            logger.debug(
                "Commit retry %d/%d%s after lock contention; sleeping %.2fs",
                attempt,
                _COMMIT_RETRY_ATTEMPTS,
                f" ({context})" if context else "",
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, _COMMIT_RETRY_MAX)
    return False


async def _get_session(
    engine: A[AsyncEngine, Depends(get_engine)],
) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine) as session:
        try:
            yield session
            ok = await commit_with_retry(session, context="request")
            if not ok:
                # Lock contention exceeded our retry budget. Re-raise as
                # OperationalError so FastAPI returns a 5xx and the client
                # can retry. (Going through the helper so the caller's
                # rollback in the except branch below is consistent with
                # what commit_with_retry already did.)
                from sqlalchemy.exc import OperationalError

                raise OperationalError(
                    "commit deferred: database is locked",
                    None,
                    Exception(),
                )
        except Exception:
            await session.rollback()
            raise


SessionDep = A[AsyncSession, Depends(_get_session)]
"""
FastAPI Dependency for an async database session. Automatically commits on success, rolls back on error
"""
