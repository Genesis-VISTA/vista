"""
Database engine, session factory, and FastAPI session dependency.
"""

import asyncio
import functools
import json
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Annotated as A, AsyncIterator
from fastapi import Depends
from sqlalchemy import Connection, event, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from ..config import settings
from .schema_check import schema_problems
from .seed import seed_db, sync_bundled_skills, sync_default_projects


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


def _add_missing_columns(conn: Connection) -> None:
    """
    Add columns the models declare and an existing database lacks.

    `SQLModel.metadata.create_all` creates missing *tables* but never alters
    existing ones, so a column added to a model after a database exists is
    simply absent and every query naming it fails. Replacing an installed
    VISTA with a newer build has to keep the existing state directory working
    ("Replacing the artifact preserves state"), and there is no migration
    tool in this project, so additive columns are filled in here.

    Deliberately narrow. It only ADDs columns; it never drops, renames, or
    retypes anything, and it skips any column that is NOT NULL without a
    server default, because SQLite cannot add one of those to a populated
    table. Those need a real migration -- this is not one.
    """
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())
    log = logging.getLogger(__name__)

    for table in SQLModel.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all just made it, with every column.
        present = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            if not column.nullable and column.server_default is None:
                log.warning(
                    "Column %s.%s is missing from the database and cannot be "
                    "added automatically (NOT NULL with no server default). "
                    "It needs a migration.",
                    table.name,
                    column.name,
                )
                continue
            column_type = column.type.compile(conn.dialect)
            default = ""
            if column.server_default is not None:
                # Existing rows take the default, so they read the same as a new row.
                arg = getattr(column.server_default, "arg", None)
                if isinstance(arg, str):
                    default = " DEFAULT '" + arg.replace("'", "''") + "'"
                elif arg is not None:
                    default = f" DEFAULT {arg.compile(dialect=conn.dialect)}"
            log.info("Adding missing column %s.%s", table.name, column.name)
            conn.execute(
                text(
                    f'ALTER TABLE "{table.name}" '
                    f'ADD COLUMN "{column.name}" {column_type}{default}'
                )
            )


DATABASE_INCOMPATIBLE = "database-incompatible"
INDEX_FILE_NAME = "incompatible-data-index.json"


class DatabaseIncompatible(Exception):
    """
    The database cannot serve the models and startup cannot repair it.

    Raised by `init_db` before anything is written to the file. `code` is what
    the launcher reads back, `message` is for the log and the researcher, and
    `problems` lists each `table.column` that does not match.
    """

    def __init__(self, message: str, problems: list[str]) -> None:
        super().__init__(message)
        self.code = DATABASE_INCOMPATIBLE
        self.message = message
        self.problems = problems


def _sqlite_file(database_url: str) -> Path | None:
    """The database file behind `database_url`, or None if it is not a file."""
    if not database_url.startswith("sqlite"):
        return None
    name = database_url.split("///", 1)[-1] if "///" in database_url else ""
    if not name or ":memory:" in name:
        return None
    return Path(name)


def _dashed(raw: object) -> str:
    """A UUID as the database stores it (32 hex) in its dashed form."""
    try:
        return str(uuid.UUID(str(raw)))
    except ValueError:
        return str(raw)


def _relative_to_data(stored: str, data_dir: Path) -> str:
    """`stored` relative to `data_dir` when inside it, else as stored."""
    candidate = Path(stored)
    if not candidate.is_absolute():
        return stored
    for base, target in ((data_dir, candidate), (data_dir.resolve(), candidate)):
        try:
            return target.relative_to(base).as_posix()
        except ValueError:
            continue
    try:
        return candidate.resolve().relative_to(data_dir.resolve()).as_posix()
    except ValueError, OSError:
        return stored


def _write_incompatible_index(conn: sqlite3.Connection, path: Path) -> None:
    """
    Write `incompatible-data-index.json` beside a refused database: what it
    holds, so a researcher can find their work after a reset moves it aside.

    Only SELECTs, each query on its own: a table or column the old database
    lacks leaves that list empty and never fails the refusal. Paths are
    relative to the data folder where they are inside it, since a reset moves
    the whole folder.
    """
    log = logging.getLogger(__name__)
    data_dir = path.parent

    def rows(sql: str, what: str) -> list[tuple]:
        try:
            return conn.execute(sql).fetchall()
        except sqlite3.Error:
            log.debug("Incompatible-data index: no %s to list", what, exc_info=True)
            return []

    index = {
        "database": path.name,
        "projects": [
            {"id": _dashed(i), "name": n, "files": f"volumes/{_dashed(i)}"}
            for i, n in rows("SELECT id, name FROM project", "projects")
        ],
        "skills": [
            {"name": n, "path": _relative_to_data(str(p), data_dir)}
            for n, p in rows("SELECT name, path FROM skill", "skills")
        ],
        "knowledge_bases": [
            {
                "slug": s,
                "name": n,
                "pdfs": _relative_to_data(str(d), data_dir),
            }
            for s, n, d in rows(
                "SELECT slug, name, pdfs_dir FROM knowledge_base", "knowledge bases"
            )
        ],
    }
    target = data_dir / INDEX_FILE_NAME
    try:
        target.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    except OSError:
        log.warning("Could not write the incompatible-data index %s", target)


def _check_compatible(path: Path) -> None:
    """
    Refuse, before any write, a database that cannot serve the models.

    Opens the file with plain `sqlite3` and only reads. A missing file or one
    with no tables passes. Reading may leave `-wal`/`-shm` companions; the
    database file itself is not touched.
    """
    if not path.exists():
        return
    conn = sqlite3.connect(path)
    try:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
        ).fetchone():
            return
        problems = schema_problems(conn, assume_additive=True)
        if not problems:
            return
        _write_incompatible_index(conn, path)
    finally:
        conn.close()
    listed = "; ".join(problems)
    raise DatabaseIncompatible(
        f"The database {path} does not match this version of VISTA and was "
        f"left unchanged: {listed}. In the VISTA window, Back up and reset "
        "moves this data aside and starts fresh; without the window, move the "
        "data folder's contents aside or delete vista.db.",
        problems,
    )


async def init_db() -> None:
    """
    Create tables and seed defaults. Call once at app startup.

    A SQLite file is checked first, over a plain connection before the engine
    exists; see `_check_compatible`. Other databases are not checked.
    """
    path = _sqlite_file(settings.database_url)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(_check_compatible, path)
    else:
        logging.getLogger(__name__).info(
            "Database is not a SQLite file; the compatibility check is skipped."
        )
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        await conn.run_sync(_add_missing_columns)

    await seed_db(engine)
    # After seeding, so seed_db's empty-database check still sees an empty one.
    from ..services import local_settings

    async with AsyncSession(engine) as session:
        await local_settings.ensure(session)
        await session.commit()
    # The AI-safety default reaches first runs and upgrades alike, after seed_db so
    # its empty-database check is unaffected.
    await sync_default_projects(engine)
    # seed_db is first-run only; this picks up skills bundled since then.
    await sync_bundled_skills(engine)


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
