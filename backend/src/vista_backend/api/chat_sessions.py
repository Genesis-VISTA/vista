import uuid

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from ..db.db import SessionDep
from ..db.schemas import (
    ChatSessionCreate,
    ChatSessionPublic,
    ChatSessionSummary,
    ChatSessionUpdate,
)
from ..services import chat_session as chat_session_service
from ..agents.agents import agent_volume_root
from ..services import background_tasks
from ..services import displayed_files as displayed_files_service
from ..services import inbox as inbox_service
from ..services import project as project_service
from ..services.chat_run import chat_run_registry, live_status
from ..services.displayed_files import DisplayedFileInfo


router = APIRouter(prefix="/projects", tags=["chat-sessions"])


async def _public(session, row) -> ChatSessionPublic:
    public = ChatSessionPublic.model_validate(row, from_attributes=True)
    live = live_status(chat_run_registry.get(row.id))
    if live is None and await inbox_service.waiting_on_researcher(session, [row.id]):
        live = "needs_you"  # a task result waits for the researcher's next message
    public.run_status = live or (row.run_state if row.run_unseen else "idle")
    return public


@router.get("/{project_name}/chat-sessions")
async def list_chat_sessions(
    project_name: str,
    session: SessionDep,
) -> list[ChatSessionSummary]:
    project = await project_service.get_project_by_name(session, project_name)
    chat_sessions = await chat_session_service.list_chat_sessions(
        session,
        project_id=project.id,
    )
    return [
        ChatSessionSummary.model_validate(row, from_attributes=True)
        for row in chat_sessions
    ]


@router.post("/{project_name}/chat-sessions", status_code=201)
async def create_chat_session(
    project_name: str,
    body: ChatSessionCreate,
    session: SessionDep,
) -> ChatSessionPublic:
    project = await project_service.get_project_by_name(session, project_name)
    chat_session = await chat_session_service.create_chat_session(
        session,
        project_id=project.id,
        payload=body,
    )
    return await _public(session, chat_session)


@router.get("/{project_name}/chat-session")
async def get_chat_session(
    project_name: str,
    session: SessionDep,
    chat_session_id: uuid.UUID | None = None,
) -> ChatSessionPublic:
    project = await project_service.get_project_by_name(session, project_name)
    chat_session = await chat_session_service.get_or_create_chat_session(
        session,
        project_id=project.id,
        chat_session_id=chat_session_id,
    )
    return await _public(session, chat_session)


@router.put("/{project_name}/chat-session")
async def put_chat_session(
    project_name: str,
    body: ChatSessionUpdate,
    session: SessionDep,
    chat_session_id: uuid.UUID | None = None,
) -> ChatSessionPublic:
    project = await project_service.get_project_by_name(session, project_name)
    chat_session = await chat_session_service.update_chat_session(
        session,
        project_id=project.id,
        updates=body,
        chat_session_id=chat_session_id,
    )
    return await _public(session, chat_session)


@router.delete("/{project_name}/chat-session", status_code=204)
async def delete_chat_session(
    project_name: str,
    session: SessionDep,
    chat_session_id: uuid.UUID,
    confirm: bool = False,
):
    """
    Delete a conversation. One with background commands running or job watches
    active is refused with 409 `live_tasks` and their list, unless `confirm`:
    then they are stopped (a watched job is left as it is) and its commands'
    logs are removed with it.
    """
    project = await project_service.get_project_by_name(session, project_name)
    row = await chat_session_service.get_chat_session(
        session, project_id=project.id, chat_session_id=chat_session_id
    )
    live = await background_tasks.live_tasks(session, chat_session_id)
    if live and not confirm:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "This conversation has background tasks running.",
                "code": "live_tasks",
                "tasks": [
                    {
                        "id": str(t.id),
                        "label": t.label,
                        "kind": t.kind,
                        "state": t.state,
                        "command": t.command,
                        "job_id": t.job_id,
                        "cluster": t.cluster,
                    }
                    for t in live
                ],
            },
        )
    project_id = project.id  # read before the commit expires it
    # Quiet first, so the results of the tasks stopped below start no turn.
    row.quiet = True
    session.add(row)
    await session.commit()
    await background_tasks.end_for_deletion(
        chat_session_id, agent_volume_root(project_id)
    )
    # A run writes to the row it belongs to, so end it before the row goes.
    await chat_run_registry.stop(chat_session_id, "stopped")
    await chat_session_service.delete_chat_session(
        session,
        project_id=project_id,
        chat_session_id=chat_session_id,
    )
    return Response(status_code=204)


@router.get("/{project_name}/chat-sessions/{chat_session_id}/displayed-files")
async def list_displayed_files(
    project_name: str,
    chat_session_id: uuid.UUID,
    session: SessionDep,
) -> list[DisplayedFileInfo]:
    return await displayed_files_service.list_displayed_files(
        session, project_name, chat_session_id
    )
