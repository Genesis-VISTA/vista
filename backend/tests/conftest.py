"""Shared fixtures for backend tests: an in-memory DB session and seed users."""

import os
import uuid

os.environ.setdefault("VISTA_BACKEND_MODEL", "test")

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
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
async def engine(tmp_path_factory):
    """
    The in-memory engine behind `session`.

    Exposed separately so a test can open a *second* session on the same
    database — which anything testing concurrent readers and writers needs, since
    two sessions on one connection would just share a transaction and never see
    each other's commits.
    """
    # A file in a temp directory rather than `sqlite://` on a StaticPool. The
    # in-memory form has to pin every session to one connection to keep the same
    # database, which means concurrent sessions share a transaction: they see
    # each other's uncommitted rows, and they interleave inside it. Both are
    # untrue of the real deployment, and both have produced misleading results
    # here — a test that passed with a commit removed, and an intermittent
    # "Could not refresh instance" in the streaming test.
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path_factory.mktemp('db') / 'test.db'}",
        connect_args={"check_same_thread": False},
    )

    # Match production: SQLite needs FK enforcement on for ON DELETE CASCADE to fire.
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fks(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine):
    """A fresh in-memory SQLite DB with all tables created, shared across the test."""
    async with AsyncSession(engine) as s:
        yield s


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


@pytest.fixture
def git_ok(monkeypatch):
    """Treat this host as having a usable git, whatever it has."""
    from vista_backend.agents.forum import project_forum
    from vista_backend.services.git_check import GitCheck

    found = GitCheck(ok=True, path="/usr/bin/git", version=(2, 50))
    monkeypatch.setattr(project_forum, "git_status", lambda: found)
    return found


@pytest.fixture
def fake_forum(monkeypatch, git_ok):
    """
    Every project's forum is the in-memory `FakeForumClient`.

    For tests about what VISTA does with a thread. The real client is tested
    against real git in `test_forum_git.py`.
    """
    from harness import fake_forum as module
    from vista_backend.agents.forum import project_forum

    module.reset()
    monkeypatch.setattr(project_forum, "client_factory", module.FakeForumClient)
    yield module
    module.reset()


@pytest.fixture
def client(tmp_path, fake_forum):
    """A forum client on a throwaway forum."""
    from vista_backend.config import ForumSettings

    return fake_forum.FakeForumClient(
        ForumSettings(enabled=True, repo_root=tmp_path / "forum", timeout=30.0)
    )
