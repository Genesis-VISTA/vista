"""
Slack settings for the desktop app's Settings → Slack section.

The tokens are write-only: they go to the OS secret store and are only ever
reported as set or not. Everything Slack-related is changed from here, never
from Slack itself — the limits and unlocking especially, so a lost phone cannot
loosen them.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db.db import SessionDep
from ..services.auth import UserDep
from ..slack import store
from ..utils import secret_store

router = APIRouter(prefix="/slack", tags=["slack"])


class SlackConfigView(BaseModel):
    enabled: bool
    handle_here: bool
    has_bot_token: bool
    has_app_token: bool
    secret_store_available: bool
    secret_store_problem: str | None
    allowed_team_ids: list[str]
    allowed_channel_ids: list[str]
    max_nodes: int
    max_walltime_minutes: int
    max_jobs_per_day: int
    locked: bool
    locked_at: str | None
    locked_by: str | None
    prevent_sleep: bool
    notify_desktop_jobs: bool


class SlackConfigUpdate(BaseModel):
    """Fields left out are kept. A token of "" removes it; null keeps it."""

    enabled: bool | None = None
    handle_here: bool | None = None
    bot_token: str | None = None
    app_token: str | None = None
    allowed_team_ids: list[str] | None = None
    allowed_channel_ids: list[str] | None = None
    max_nodes: int | None = Field(default=None, ge=1)
    max_walltime_minutes: int | None = Field(default=None, ge=1)
    max_jobs_per_day: int | None = Field(default=None, ge=0)
    locked: bool | None = None
    prevent_sleep: bool | None = None
    notify_desktop_jobs: bool | None = None


def _ids(values: list[str]) -> list[str]:
    return sorted({v.strip().upper() for v in values if v.strip()})


async def _view(session) -> SlackConfigView:
    row = await store.get_config(session)
    ok, problem = secret_store.available()
    return SlackConfigView(
        enabled=row.enabled,
        handle_here=row.handle_here,
        has_bot_token=bool(store.bot_token()),
        has_app_token=bool(store.app_token()),
        secret_store_available=ok,
        secret_store_problem=problem,
        allowed_team_ids=row.allowed_team_ids,
        allowed_channel_ids=row.allowed_channel_ids,
        max_nodes=row.max_nodes,
        max_walltime_minutes=row.max_walltime_minutes,
        max_jobs_per_day=row.max_jobs_per_day,
        locked=row.locked,
        locked_at=row.locked_at,
        locked_by=row.locked_by,
        prevent_sleep=row.prevent_sleep,
        notify_desktop_jobs=row.notify_desktop_jobs,
    )


@router.get("/config")
async def get_slack_config(session: SessionDep, user: UserDep) -> SlackConfigView:
    view = await _view(session)
    await session.commit()
    return view


@router.put("/config")
async def update_slack_config(
    updates: SlackConfigUpdate, session: SessionDep, user: UserDep
) -> SlackConfigView:
    row = await store.get_config(session)
    fields = updates.model_dump(
        exclude_unset=True, exclude={"bot_token", "app_token", "locked"}
    )
    if "allowed_team_ids" in fields:
        fields["allowed_team_ids"] = _ids(fields["allowed_team_ids"] or [])
    if "allowed_channel_ids" in fields:
        fields["allowed_channel_ids"] = _ids(fields["allowed_channel_ids"] or [])
    fields = {k: v for k, v in fields.items() if v is not None}

    # Checked against the settings as they would be, before anything is
    # written, so a refused save changes nothing.
    if fields.get("enabled", row.enabled):
        problem = store.why_not_enabled(bot=updates.bot_token, app=updates.app_token)
        if problem is None and not fields.get("allowed_team_ids", row.allowed_team_ids):
            problem = "Slack needs at least one allowed workspace ID first."
        if problem is not None:
            raise HTTPException(status_code=400, detail=problem)
    try:
        store.set_tokens(bot=updates.bot_token, app=updates.app_token)
    except store.SlackConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    for name, value in fields.items():
        setattr(row, name, value)
    if updates.locked is not None and updates.locked != row.locked:
        row.locked = updates.locked
        row.locked_at = store.now_iso() if updates.locked else None
        row.locked_by = f"desktop:{user.email}" if updates.locked else None

    session.add(row)
    await session.commit()
    return await _view(session)
