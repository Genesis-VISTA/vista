"""
The settings, read and written here: the installation's (one `local_settings`
row, made at startup by `ensure`) and the current user's (a `user_settings` row,
see `services/settings_view`), merged into a `SettingsView`.

A desktop install has one researcher, `local`; a hosted deployment has one per
user, and its server settings come from the environment, not the row.
"""

from typing import Annotated as A

from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import settings
from ..db.db import SessionDep
from ..db.schemas import (
    LOCAL_SETTINGS_ID,
    SECRET_SETTINGS_FIELDS,
    LocalSettingsPublic,
    LocalSettingsTable,
    LocalSettingsUpdate,
)
from .current_user import CurrentUser
from .hosted import is_hosted
from .settings_view import (
    HOSTED_SERVER_VALUES,
    SERVER_SETTINGS_REASON,
    SettingsView,
    load,
    server_managed_fields,
)

WRITE_ONLY_TOKENS = SECRET_SETTINGS_FIELDS
"""Fields that are never read back, so a save that sends null keeps them."""

AGENT_IRRELEVANT = {"update_checks", "update_prereleases", "collaboration_enabled"}
"""Fields no agent reads, so changing them leaves the built agents alone."""


async def ensure(session: AsyncSession) -> LocalSettingsTable:
    """The settings row, created (with a new `install_id`) if there is none yet."""
    row = await session.get(LocalSettingsTable, LOCAL_SETTINGS_ID)
    if row is None:
        row = LocalSettingsTable(id=LOCAL_SETTINGS_ID)
        session.add(row)
        await session.flush()
    return row


async def get(session: AsyncSession, user_id: str | None = None) -> SettingsView:
    """
    The server's settings and a user's (`user_id`, else the current user's: see
    `settings_view.load`). Never creates the server row: `init_db` does, once,
    at startup, so concurrent reads cannot race each other into making two.
    """
    return await load(session, user_id)


async def get_server(session: AsyncSession) -> SettingsView:
    """
    The installation's settings alone, for code that has no user and reads no
    user field (the update check's schedule). The view's user half is an empty
    stand-in that is never saved.
    """
    from ..db.schemas import UserSettingsTable

    server = await session.get(LocalSettingsTable, LOCAL_SETTINGS_ID)
    if server is None:
        raise RuntimeError("The settings row is missing; init_db creates it.")
    return SettingsView(server, UserSettingsTable(user_id=""))


async def update(
    session: AsyncSession, updates: LocalSettingsUpdate, user_id: str | None = None
) -> SettingsView:
    """
    Save `updates`: the user's fields to their row, the server's to the
    installation's. Hosted, the server's come from the environment, so they are
    left out; the response's `set_by_server` says so.
    """
    row = await get(session, user_id)
    changes = updates.model_dump(exclude_unset=True)
    if is_hosted():
        for key in HOSTED_SERVER_VALUES:
            changes.pop(key, None)
    changed = set(changes)
    if (
        "collaboration_server_url" in changes
        and changes["collaboration_server_url"] != row.collaboration_server_url
    ):
        # The token is the old address's: never send it to the new one.
        row.collaboration_token = None
    for key in WRITE_ONLY_TOKENS:
        # Write-only: null keeps the stored token (the client never has it to
        # send back), "" removes it.
        if key in changes:
            value = changes.pop(key)
            if value is not None:
                setattr(row, key, value or None)
    for key, value in changes.items():
        setattr(row, key, value)
    session.add(row.server_row)
    session.add(row.user_row)
    await session.flush()
    await session.refresh(row.server_row)
    await session.refresh(row.user_row)
    # A job watch paused for lost cluster access tries again on every save.
    from .background_tasks import nudge_after_commit

    nudge_after_commit(session)
    if changed - AGENT_IRRELEVANT:
        # Every agent was built from these settings (the update switches are
        # not ones they read). Imported here because the agent pool reads this module to
        # build an agent.
        from .project_agent import invalidate_agents

        invalidate_agents(session)
    return row


def to_public(row: SettingsView | LocalSettingsTable) -> LocalSettingsPublic:
    """The settings as the API shows them, with what comes from configuration, not the row."""
    public = LocalSettingsPublic.model_validate(row)
    public.onboarding_tour_enabled = settings.onboarding_tour
    managed = server_managed_fields()
    public.set_by_server = managed
    public.set_by_server_reason = SERVER_SETTINGS_REASON if managed else None
    return public


async def get_public(
    session: AsyncSession, user_id: str | None = None
) -> LocalSettingsPublic:
    """
    The settings as agents and routes read them: no write-only tokens. A plain
    `AsyncSession` caller acts as the current user (`current_user_id`).
    """
    return to_public(await get(session, user_id))


async def _public_for_request(
    session: SessionDep, user: CurrentUser
) -> LocalSettingsPublic:
    return await get_public(session, user.id)


LocalSettingsDep = A[LocalSettingsPublic, Depends(_public_for_request)]
"""The settings (server's and the signed-in user's), for a route that reads them."""
