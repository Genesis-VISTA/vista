"""Watching, stopping and listing chat runs (see `services/chat_run.py`)."""

import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sse_starlette.event import ServerSentEvent
from sse_starlette.sse import EventSourceResponse

from ..db.db import SessionDep
from ..db.schemas import InboxItemEdit, InboxItemPublic
from ..services import chat_session as chat_session_service
from ..services import inbox as inbox_service
from ..services import project as project_service
from ..services.chat_run import chat_run_registry, row_status

router = APIRouter(prefix="/projects", tags=["chat-runs"])


class ChatRunStatus(BaseModel):
    chat_session_id: uuid.UUID
    status: str
    """ `working`, `needs_you`, `done`, `failed`, `interrupted`; idle conversations are omitted. """
    unseen: bool


@router.get("/{project_name}/chat-sessions/{chat_session_id}/run/events")
async def watch_chat_run(
    project_name: str,
    chat_session_id: uuid.UUID,
    session: SessionDep,
    after: int = 0,
):
    """
    Watch a conversation's active run: every event with `seq > after` (so `after=0` replays it
    from the start), then live events until the run ends. The SSE `id:` is the event's seq.
    Answers 204 when the conversation has no active run.
    """
    project = await project_service.get_project_by_name(session, project_name)
    await chat_session_service.get_chat_session(
        session,
        project_id=project.id,
        chat_session_id=chat_session_id,
    )
    run = chat_run_registry.get(chat_session_id)
    if run is None:
        return Response(status_code=204)

    async def watch() -> AsyncGenerator[ServerSentEvent, None]:
        async for event in run.subscribe(after):
            yield ServerSentEvent(event=event.kind, data=event.data, id=str(event.seq))

    return EventSourceResponse(watch())


@router.post(
    "/{project_name}/chat-sessions/{chat_session_id}/run/stop", status_code=202
)
async def stop_chat_run(
    project_name: str,
    chat_session_id: uuid.UUID,
    session: SessionDep,
) -> dict[str, bool]:
    """Stop a conversation's active run. It is saved as stopped, keeping the tool calls that completed."""
    project = await project_service.get_project_by_name(session, project_name)
    await chat_session_service.get_chat_session(
        session,
        project_id=project.id,
        chat_session_id=chat_session_id,
    )
    if not await chat_run_registry.stop(chat_session_id, "stopped", wait=False):
        raise HTTPException(status_code=404, detail="No active run")
    return {"ok": True}


@router.get("/{project_name}/chat-runs/status")
async def chat_runs_status(
    project_name: str,
    session: SessionDep,
) -> list[ChatRunStatus]:
    """Every conversation in the project that is working, needs the researcher, or has an unseen outcome."""
    project = await project_service.get_project_by_name(session, project_name)
    rows = await chat_session_service.list_chat_sessions(session, project_id=project.id)
    waiting = await inbox_service.waiting_on_researcher(session, [r.id for r in rows])
    statuses = []
    for row in rows:
        status = row_status(row, waiting=row.id in waiting)
        if status != "idle":
            statuses.append(
                ChatRunStatus(
                    chat_session_id=row.id,
                    status=status,
                    unseen=row.run_unseen and status not in ("working", "needs_you"),
                )
            )
    return statuses


# ---------------------------------------------------------------------------
# The conversation's inbox (openspec change background-tasks)
# ---------------------------------------------------------------------------


def _item(item) -> InboxItemPublic:
    return InboxItemPublic.model_validate(item, from_attributes=True)


def _refused(item, detail: str, code: str) -> JSONResponse:
    # Carries the item as it now is, so the view can show it as delivered.
    return JSONResponse(
        status_code=409,
        content={"detail": detail, "code": code, "item": _item(item).model_dump()},
    )


async def _queued_message(session, chat_session_id: uuid.UUID, item_id: int):
    try:
        item = await inbox_service.get(session, chat_session_id, item_id)
    except inbox_service.ItemNotFound:
        raise HTTPException(status_code=404, detail="No such queued message")
    if item.kind != "message":
        return None, _refused(
            item, "Only a queued message can be changed.", "not_a_message"
        )
    return item, None


@router.get("/{project_name}/chat-sessions/{chat_session_id}/inbox")
async def list_inbox(
    project_name: str,
    chat_session_id: uuid.UUID,
    session: SessionDep,
) -> list[InboxItemPublic]:
    """What is queued for the conversation's agent, in arrival order."""
    project = await project_service.get_project_by_name(session, project_name)
    await chat_session_service.get_chat_session(
        session, project_id=project.id, chat_session_id=chat_session_id
    )
    return [_item(i) for i in await inbox_service.list_queued(session, chat_session_id)]


@router.patch(
    "/{project_name}/chat-sessions/{chat_session_id}/inbox/{item_id}",
    response_model=InboxItemPublic,
)
async def edit_inbox_item(
    project_name: str,
    chat_session_id: uuid.UUID,
    item_id: int,
    body: InboxItemEdit,
    session: SessionDep,
):
    """Change a queued message's text. 409 once the agent has taken it, with the item as delivered."""
    project = await project_service.get_project_by_name(session, project_name)
    await chat_session_service.get_chat_session(
        session, project_id=project.id, chat_session_id=chat_session_id
    )
    _, refused = await _queued_message(session, chat_session_id, item_id)
    if refused is not None:
        return refused
    try:
        item = await inbox_service.edit(session, chat_session_id, item_id, body.text)
    except inbox_service.ItemTaken as taken:
        return _refused(
            taken.item, "The agent has already taken this message.", "inbox_item_taken"
        )
    await chat_run_registry.announce_inbox(chat_session_id)
    return _item(item)


@router.delete(
    "/{project_name}/chat-sessions/{chat_session_id}/inbox/{item_id}", status_code=204
)
async def delete_inbox_item(
    project_name: str,
    chat_session_id: uuid.UUID,
    item_id: int,
    session: SessionDep,
):
    """Delete a queued message so the agent never receives it. 409 once it has been taken."""
    project = await project_service.get_project_by_name(session, project_name)
    await chat_session_service.get_chat_session(
        session, project_id=project.id, chat_session_id=chat_session_id
    )
    _, refused = await _queued_message(session, chat_session_id, item_id)
    if refused is not None:
        return refused
    try:
        await inbox_service.withdraw(session, chat_session_id, item_id)
    except inbox_service.ItemTaken as taken:
        return _refused(
            taken.item, "The agent has already taken this message.", "inbox_item_taken"
        )
    await chat_run_registry.announce_inbox(chat_session_id)
    return Response(status_code=204)
