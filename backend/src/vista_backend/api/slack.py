"""
Slack settings for the desktop app's Settings → Slack section.

The tokens are write-only: they go to the OS secret store and are only ever
reported as set or not. Everything Slack-related is changed from here, never
from Slack itself — the limits and unlocking especially, so a lost phone cannot
loosen them.
"""

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db.db import SessionDep
from ..db.schemas import SlackLinkTable
from ..services import project as project_service
from ..services.auth import UserDep
from ..slack import access, linking, store
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


class SlackLinkView(BaseModel):
    id: uuid.UUID
    team_id: str
    slack_user_id: str
    project_id: uuid.UUID | None
    notify_in_thread: bool
    created_at: str


class SlackLinkCreate(BaseModel):
    code: str


class SlackLinkUpdate(BaseModel):
    project_id: uuid.UUID | None = None
    notify_in_thread: bool | None = None


def _link_view(link: SlackLinkTable) -> SlackLinkView:
    return SlackLinkView.model_validate(link, from_attributes=True)


async def _own_link(session, user, link_id: uuid.UUID) -> SlackLinkTable:
    link = await session.get(SlackLinkTable, link_id)
    if link is None or link.user_id != user.id:
        raise HTTPException(status_code=404, detail="No such Slack link.")
    return link


@router.get("/links")
async def list_slack_links(session: SessionDep, user: UserDep) -> list[SlackLinkView]:
    """The Slack accounts linked to the signed-in user."""
    return [_link_view(link) for link in await access.links_for_user(session, user.id)]


@router.post("/links", status_code=201)
async def create_slack_link(
    body: SlackLinkCreate, session: SessionDep, user: UserDep
) -> SlackLinkView:
    """Link the Slack account a code was shown to, to the signed-in user."""
    try:
        link = await linking.redeem_code(session, code=body.code, user_id=user.id)
    except linking.LinkError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _link_view(link)


@router.put("/links/{link_id}")
async def update_slack_link(
    link_id: uuid.UUID, body: SlackLinkUpdate, session: SessionDep, user: UserDep
) -> SlackLinkView:
    link = await _own_link(session, user, link_id)
    if "project_id" in body.model_fields_set:
        if body.project_id is not None:
            mine = {p.id for p in await project_service.list_projects(session, user)}
            if body.project_id not in mine:
                raise HTTPException(status_code=400, detail="No such project.")
        link.project_id = body.project_id
    if body.notify_in_thread is not None:
        link.notify_in_thread = body.notify_in_thread
    session.add(link)
    await session.commit()
    await session.refresh(link)
    return _link_view(link)


@router.delete("/links/{link_id}", status_code=204)
async def delete_slack_link(
    link_id: uuid.UUID, session: SessionDep, user: UserDep
) -> None:
    link = await _own_link(session, user, link_id)
    await session.delete(link)
    await session.commit()
