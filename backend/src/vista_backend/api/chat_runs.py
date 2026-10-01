"""Watching, stopping and listing chat runs (see `services/chat_run.py`)."""

import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sse_starlette.event import ServerSentEvent
from sse_starlette.sse import EventSourceResponse

from ..db.db import SessionDep
from ..services import chat_session as chat_session_service
from ..services import project as project_service
from ..services.auth import UserDep
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
    user: UserDep,
    after: int = 0,
):
    """
    Watch a conversation's active run: every event with `seq > after` (so `after=0` replays it
    from the start), then live events until the run ends. The SSE `id:` is the event's seq.
    Answers 204 when the conversation has no active run.
    """
    project = await project_service.get_project_by_name(session, project_name, user)
    await chat_session_service.get_chat_session(
        session,
        project_id=project.id,
        user_id=user.id,
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
    user: UserDep,
) -> dict[str, bool]:
    """Stop a conversation's active run. It is saved as stopped, keeping the tool calls that completed."""
    project = await project_service.get_project_by_name(session, project_name, user)
    await chat_session_service.get_chat_session(
        session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat_session_id,
    )
    if not await chat_run_registry.stop(chat_session_id, "stopped", wait=False):
        raise HTTPException(status_code=404, detail="No active run")
    return {"ok": True}


@router.get("/{project_name}/chat-runs/status")
async def chat_runs_status(
    project_name: str,
    session: SessionDep,
    user: UserDep,
) -> list[ChatRunStatus]:
    """Every conversation in the project that is working, needs the researcher, or has an unseen outcome."""
    project = await project_service.get_project_by_name(session, project_name, user)
    rows = await chat_session_service.list_chat_sessions(
        session, project_id=project.id, user_id=user.id
    )
    statuses = []
    for row in rows:
        status = row_status(row)
        if status != "idle":
            statuses.append(
                ChatRunStatus(
                    chat_session_id=row.id,
                    status=status,
                    unseen=row.run_unseen and status not in ("working", "needs_you"),
                )
            )
    return statuses
