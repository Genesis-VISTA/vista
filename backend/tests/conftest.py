"""Shared fixtures for backend tests: an in-memory DB session and seed users."""

import os
import uuid

os.environ.setdefault("VISTA_BACKEND_MODEL", "test")

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.db.schemas import UserPublicWithConfig, UserTable


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Opt-in gates for live / real-HPC tests (Milestone D validation lane).

    Markers alone keep PR CI hermetic via ``-m "not live and not hpc and not sandbox"``.
    These skips also protect bare ``pytest`` runs without the marker filter.
    """
    run_live = os.environ.get("VISTA_RUN_LIVE") == "1"
    run_hpc = os.environ.get("VISTA_RUN_HPC") == "1"
    skip_live = pytest.mark.skip(reason="set VISTA_RUN_LIVE=1 to run live tests")
    skip_hpc = pytest.mark.skip(reason="set VISTA_RUN_HPC=1 to run real-HPC tests")
    for item in items:
        if item.get_closest_marker("live") is not None and not run_live:
            item.add_marker(skip_live)
        if item.get_closest_marker("hpc") is not None and not run_hpc:
            item.add_marker(skip_hpc)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _no_inherited_vista_data(monkeypatch):
    """
    Detach every test from whatever vista-data source the host happens to have
    configured.

    Seeding picks its source from two settings -- a bundled payload directory
    and a GitLab token -- and both are read from the environment, including from
    any `.env` above the working directory. A packaging build machine has the
    payload path exported, which would silently move the offline seeding tests
    onto the payload branch. Tests that want a source set one themselves; this
    only removes the ambient one.
    """
    from vista_backend.config import settings

    monkeypatch.setattr(settings, "vista_data_payload_dir", None)
    monkeypatch.setattr(settings, "vista_data_token", None)


@pytest.fixture
async def session():
    """A fresh in-memory SQLite DB with all tables created, shared across the test."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Match production: SQLite needs FK enforcement on for ON DELETE CASCADE to fire.
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fks(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    async with AsyncSession(engine) as s:
        yield s
    await engine.dispose()


async def _make_user(session: AsyncSession, *, is_admin: bool) -> UserPublicWithConfig:
    row = UserTable(
        id=uuid.uuid4(), email=f"{uuid.uuid4()}@example.com", is_admin=is_admin
    )
    session.add(row)
    await session.flush()
    return UserPublicWithConfig.model_validate(row)


@pytest.fixture
async def admin(session) -> UserPublicWithConfig:
    return await _make_user(session, is_admin=True)


@pytest.fixture
async def alice(session) -> UserPublicWithConfig:
    return await _make_user(session, is_admin=False)


@pytest.fixture
async def bob(session) -> UserPublicWithConfig:
    return await _make_user(session, is_admin=False)
