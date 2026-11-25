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

from ..services.current_user import CurrentUser
from ..db.db import SessionDep
from ..db.ownership import get_owned
from ..db.ownership import uid
from ..db.schemas import SlackLinkTable, UserTable
from ..services import project as project_service
from ..services import collaboration
from ..services.hosted import is_hosted, refuse_when_hosted
from ..services.local_settings import LocalSettingsDep
from ..slack import access, limits, linking, store
from ..slack import service as slack_service
from ..utils import secret_store

router = APIRouter(prefix="/slack", tags=["slack"])


class SlackConfigView(BaseModel):
    enabled: bool
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
    connection: str
    """ off · connected · error. """
    connection_detail: str | None
    server_managed: bool = False
    """ Hosted: the app's tokens, allowlists and limits are the server's, not shown or changed here. """


class SlackConfigUpdate(BaseModel):
    """Fields left out are kept. A token of "" removes it; null keeps it."""

    enabled: bool | None = None
    bot_token: str | None = None
    app_token: str | None = None
    allowed_team_ids: list[str] | None = None
    allowed_channel_ids: list[str] | None = None
    max_nodes: int | None = Field(default=None, ge=1)
    max_walltime_minutes: int | None = Field(default=None, ge=1)
    max_jobs_per_day: int | None = Field(default=None, ge=0)
    locked: bool | None = None


def _ids(values: list[str]) -> list[str]:
    return sorted({v.strip().upper() for v in values if v.strip()})


async def _view(session, user: UserTable) -> SlackConfigView:
    row = await store.get_config(session)
    state, detail = slack_service.status()
    if is_hosted():
        # The app is the server's: a researcher sees whether Slack works and
        # their own lock, nothing of its tokens or allowlists.
        return SlackConfigView(
            enabled=row.enabled,
            has_bot_token=False,
            has_app_token=False,
            secret_store_available=True,
            secret_store_problem=None,
            allowed_team_ids=[],
            allowed_channel_ids=[],
            max_nodes=row.max_nodes,
            max_walltime_minutes=row.max_walltime_minutes,
            max_jobs_per_day=row.max_jobs_per_day,
            locked=row.locked or await limits.user_locked(session, uid(user)),
            locked_at=row.locked_at,
            locked_by=row.locked_by,
            connection=state,
            connection_detail=detail,
            server_managed=True,
        )
    ok, problem = secret_store.available()
    return SlackConfigView(
        enabled=row.enabled,
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
        connection=state,
        connection_detail=detail,
    )


@router.get("/config")
async def get_slack_config(session: SessionDep, user: CurrentUser) -> SlackConfigView:
    view = await _view(session, user)
    await session.commit()
    return view


@router.put("/config")
async def update_slack_config(
    updates: SlackConfigUpdate, session: SessionDep, user: CurrentUser
) -> SlackConfigView:
    refuse_when_hosted("Changing the Slack app's settings")
    row = await store.get_config(session)
    fields = updates.model_dump(
        exclude_unset=True,
        exclude={"bot_token", "app_token", "locked"},
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
        row.locked_by = "desktop" if updates.locked else None

    session.add(row)
    await session.commit()
    # Locking needs no reconnect: the gate reads the lock on every call. The
    # Home tab shows the lock, so it is redrawn now rather than within 5 minutes.
    if updates.locked is not None:
        await slack_service.refresh_homes()
    if set(updates.model_fields_set) - {
        "locked",
        "max_nodes",
        "max_walltime_minutes",
        "max_jobs_per_day",
    }:
        await slack_service.restart()
    return await _view(session, user)


class SlackLinkView(BaseModel):
    id: uuid.UUID
    team_id: str
    slack_user_id: str
    collab_name: str | None
    """ Who the researcher was on the collaboration server when linked, if signed in. """
    project_id: uuid.UUID | None
    locked: bool = False
    """ Hosted: this user's own lock, set from Slack and cleared here. """
    created_at: str


class SlackLinkCreate(BaseModel):
    code: str


class SlackLinkUpdate(BaseModel):
    project_id: uuid.UUID | None = None
    locked: bool | None = None
    """ Only `false` does anything: a lock is set from Slack, and undone here. """


def _link_view(link: SlackLinkTable) -> SlackLinkView:
    return SlackLinkView.model_validate(link, from_attributes=True)


async def _link(session, link_id: uuid.UUID, user: CurrentUser) -> SlackLinkTable:
    """The user's own link; another user's is a 404, as one that does not exist."""
    link = await get_owned(session, SlackLinkTable, link_id, user)
    if link is None:
        raise HTTPException(status_code=404, detail="No such Slack link.")
    return link


@router.get("/links")
async def list_slack_links(
    session: SessionDep, user: CurrentUser
) -> list[SlackLinkView]:
    """The Slack accounts the current user linked."""
    return [_link_view(link) for link in await access.all_links(session, user)]


@router.post("/links", status_code=201)
async def create_slack_link(
    body: SlackLinkCreate,
    session: SessionDep,
    local: LocalSettingsDep,
    user: CurrentUser,
) -> SlackLinkView:
    """
    Link the Slack account a code was shown to, to the current user.

    Records who the researcher is on the collaboration server, when signed in
    there, for a hosted router to route by later; linking never depends on it.
    """
    me = (await collaboration.status(local)).user
    try:
        link = await linking.redeem_code(
            session,
            code=body.code,
            user_id=uid(user),
            collab_sub=me.sub if me else None,
            collab_name=(me.name or me.email) if me else None,
        )
    except linking.LinkError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _link_view(link)


@router.put("/links/{link_id}")
async def update_slack_link(
    link_id: uuid.UUID, body: SlackLinkUpdate, session: SessionDep, user: CurrentUser
) -> SlackLinkView:
    link = await _link(session, link_id, user)
    if "project_id" in body.model_fields_set:
        if body.project_id is not None:
            mine = {p.id for p in await project_service.list_projects(session, user)}
            if body.project_id not in mine:
                raise HTTPException(status_code=400, detail="No such project.")
        link.project_id = body.project_id
    if body.locked is False and link.locked:
        link.locked = False
        link.locked_at = None
    session.add(link)
    await session.commit()
    await session.refresh(link)
    return _link_view(link)


@router.delete("/links/{link_id}", status_code=204)
async def delete_slack_link(
    link_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> None:
    link = await _link(session, link_id, user)
    await session.delete(link)
    await session.commit()
