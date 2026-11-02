"""
The installation's settings: one row, read and written here.

VISTA has one researcher per installation, so what used to be per user —
inference keys, cluster credentials, repository tokens, the research profile —
lives on `LocalSettingsTable`, made at startup by `ensure`.
"""

import uuid
from typing import Annotated as A

from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import settings
from ..db.db import SessionDep
from ..db.schemas import (
    LOCAL_SETTINGS_ID,
    LocalSettingsPublic,
    LocalSettingsTable,
    LocalSettingsUpdate,
)

WRITE_ONLY_TOKENS = ("github_token", "gitlab_token", "collaboration_token")
"""Fields that are never read back, so a save that sends null keeps them."""


async def ensure(session: AsyncSession) -> LocalSettingsTable:
    """The settings row, created (with a new `install_id`) if there is none yet."""
    row = await session.get(LocalSettingsTable, LOCAL_SETTINGS_ID)
    if row is None:
        row = LocalSettingsTable(id=LOCAL_SETTINGS_ID)
        session.add(row)
        await session.flush()
    return row


async def get(session: AsyncSession) -> LocalSettingsTable:
    """
    The settings row. Never creates one: `init_db` does, once, at startup, so
    concurrent reads cannot race each other into making two.
    """
    row = await session.get(LocalSettingsTable, LOCAL_SETTINGS_ID)
    if row is None:
        raise RuntimeError("The settings row is missing; init_db creates it.")
    return row


async def update(
    session: AsyncSession, updates: LocalSettingsUpdate
) -> LocalSettingsTable:
    row = await get(session)
    changes = updates.model_dump(exclude_unset=True)
    changed = set(changes)
    if (
        "collaboration_server_url" in changes
        and changes["collaboration_server_url"] != row.collaboration_server_url
    ):
        # The token is the old server's: never send it to the new one. Its
        # teams are the old server's too, here and on everything in them.
        row.collaboration_token = None
        row.collaboration_team_id = None
        await _forget_teams(session, changes["collaboration_server_url"])
    for key in WRITE_ONLY_TOKENS:
        # Write-only: null keeps the stored token (the client never has it to
        # send back), "" removes it.
        if key in changes:
            value = changes.pop(key)
            if value is not None:
                setattr(row, key, value or None)
    for key, value in changes.items():
        setattr(row, key, value)
    session.add(row)
    await session.flush()
    await session.refresh(row)
    if changed - {"collaboration_team_id"}:
        # Every agent was built from these settings (the team is not one they
        # read). Imported here because the agent pool reads this module to
        # build an agent.
        from .project_agent import invalidate_agents

        invalidate_agents(session)
    return row


async def _forget_teams(session: AsyncSession, url: str | None) -> None:
    """
    Make private every project, skill and knowledge base not linked to `url`,
    the new server: their teams are another server's, and mean nothing on it.
    """
    from sqlalchemy import update
    from sqlmodel import col, or_

    from ..db.schemas import KnowledgeBaseTable, ProjectTable, SkillTable

    for table in (ProjectTable, SkillTable, KnowledgeBaseTable):
        stmt = update(table).values(team_id=None)
        if url:
            server = col(table.origin_server)
            stmt = stmt.where(or_(server.is_(None), server != url))
        await session.exec(stmt)


async def selected_team(session: AsyncSession) -> uuid.UUID | None:
    """The team a new project, skill or knowledge base joins: the one selected here."""
    return (await get(session)).collaboration_team_id


def to_public(row: LocalSettingsTable) -> LocalSettingsPublic:
    """The row as the API shows it, with what comes from configuration, not the row."""
    public = LocalSettingsPublic.model_validate(row)
    public.onboarding_tour_enabled = settings.onboarding_tour
    return public


async def get_public(session: SessionDep) -> LocalSettingsPublic:
    """
    The settings as agents and routes read them: no write-only tokens. Also the
    dependency behind `LocalSettingsDep`; `SessionDep` is a plain `AsyncSession`
    to any other caller.
    """
    return to_public(await get(session))


LocalSettingsDep = A[LocalSettingsPublic, Depends(get_public)]
"""The settings, for a route that reads them."""
