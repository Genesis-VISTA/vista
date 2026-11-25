"""Fixtures for Slack tests: an enabled config and linked Slack accounts, no real Slack."""

import inspect

import pytest
from slack_sdk.web.async_client import AsyncWebClient

from vista_backend.db.schemas import SlackConfigTable, SlackLinkTable
from vista_backend.slack import store

TEAM = "T0001"
OTHER_TEAM = "T9999"


@pytest.fixture(autouse=True)
def no_real_slack(monkeypatch):
    """Any Web API call a test did not stub fails loudly instead of reaching Slack."""

    async def refuse(self, *args, **kwargs):
        raise AssertionError(f"a test tried to call Slack: {args[:1]}")

    monkeypatch.setattr(AsyncWebClient, "api_call", refuse)


@pytest.fixture(autouse=True)
def no_real_connection(request, monkeypatch):
    """
    A service started by a settings save connects through a fake, never Socket
    Mode, and an async test's is stopped when it ends.
    """
    from vista_backend.slack import service

    from .fakes import FakeTransport

    monkeypatch.setattr(service, "default_transport", lambda bot, app: FakeTransport())
    monkeypatch.setattr(service, "service", None)
    monkeypatch.setattr(service, "_held", [])
    monkeypatch.setattr(service, "_holding", False)
    if inspect.iscoroutinefunction(request.function):
        request.getfixturevalue("stop_slack_service")


@pytest.fixture
async def stop_slack_service():
    from vista_backend.slack import service

    yield
    await service.stop()


@pytest.fixture(autouse=True)
def slack_db_is_the_test_db(request, monkeypatch):
    """Slack code opens its own sessions; every one must reach the test's database."""
    from sqlmodel.ext.asyncio.session import AsyncSession

    from vista_backend.services import chat_run

    # `engine` is async, which a sync test may not depend on (pytest 8 makes
    # that an error); a sync test opens no session anyway.
    if not inspect.iscoroutinefunction(request.function):
        return
    engine = request.getfixturevalue("engine")
    monkeypatch.setattr(chat_run, "session_factory", lambda: AsyncSession(engine))
    # A job submitted from Slack is watched: its record goes to the test's
    # database too, and no watch reads a real cluster unless a test says so.
    from vista_backend.services import background_tasks

    monkeypatch.setattr(
        background_tasks, "session_factory", lambda: AsyncSession(engine)
    )
    started: list = []
    monkeypatch.setattr(
        background_tasks.background_task_registry,
        "watch_job",
        lambda record, **kw: started.append(record),
    )
    request.node.started_watches = started


@pytest.fixture(autouse=True)
def audit_log_in_tmp(tmp_path, monkeypatch):
    """The audit file goes to the test's directory, not the install's data dir."""
    from vista_backend.slack import audit

    monkeypatch.setattr(audit, "log_path", lambda: tmp_path / "slack-audit.jsonl")


@pytest.fixture
async def slack_config(session) -> SlackConfigTable:
    row = await store.get_config(session)
    row.enabled = True
    row.allowed_team_ids = [TEAM]
    session.add(row)
    await session.commit()
    await session.refresh(row)
    # A detached copy, as a handler reads it once per event; tests may change it.
    return SlackConfigTable.model_validate(row.model_dump())


async def link(
    session,
    slack_user_id: str,
    *,
    team_id: str = TEAM,
    collab_sub: str | None = None,
    user_id: str = "local",
) -> SlackLinkTable:
    row = SlackLinkTable(
        team_id=team_id,
        slack_user_id=slack_user_id,
        user_id=user_id,
        collab_sub=collab_sub,
        dm_channel_id=f"D{slack_user_id}",
        created_at=store.now_iso(),
    )
    session.add(row)
    await session.commit()
    return row


async def project(session, name: str = "molten-salts", user_id: str = "local"):
    from vista_backend.db.schemas import ProjectTable

    row = ProjectTable(user_id=user_id, name=name, skills=[], knowledge_bases=[])
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
