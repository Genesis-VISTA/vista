"""Fixtures for Slack tests: an enabled config and linked users, no real Slack."""

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
def slack_db_is_the_test_db(engine, monkeypatch):
    """Slack code opens its own sessions; every one must reach the test's database."""
    from sqlmodel.ext.asyncio.session import AsyncSession

    from vista_backend.services import chat_run

    monkeypatch.setattr(chat_run, "session_factory", lambda: AsyncSession(engine))


@pytest.fixture(autouse=True)
def audit_log_in_tmp(tmp_path, monkeypatch):
    """The audit file goes to the test's directory, not the install's data dir."""
    from vista_backend.slack import audit

    monkeypatch.setattr(audit, "log_path", lambda: tmp_path / "slack-audit.jsonl")


@pytest.fixture
async def slack_config(session) -> SlackConfigTable:
    row = await store.get_config(session)
    row.enabled = True
    row.handle_here = True
    row.allowed_team_ids = [TEAM]
    session.add(row)
    await session.commit()
    await session.refresh(row)
    # A detached copy, as a handler reads it once per event; tests may change it.
    return SlackConfigTable.model_validate(row.model_dump())


async def link(
    session, user, slack_user_id: str, *, team_id: str = TEAM
) -> SlackLinkTable:
    row = SlackLinkTable(
        team_id=team_id,
        slack_user_id=slack_user_id,
        user_id=user.id,
        dm_channel_id=f"D{slack_user_id}",
        created_at=store.now_iso(),
    )
    session.add(row)
    await session.commit()
    return row


async def project(session, user, name: str = "molten-salts"):
    from vista_backend.db.schemas import ProjectMemberTable, ProjectTable

    row = ProjectTable(name=name, skills=[], knowledge_bases=[])
    session.add(row)
    await session.flush()
    session.add(ProjectMemberTable(project_id=row.id, user_id=user.id))
    await session.commit()
    await session.refresh(row)
    return row
