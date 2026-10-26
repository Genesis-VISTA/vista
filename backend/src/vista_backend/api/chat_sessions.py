import uuid

from fastapi import APIRouter

from ..db.db import SessionDep
from ..db.schemas import (
    ChatSessionCreate,
    ChatSessionPublic,
    ChatSessionSummary,
    ChatSessionUpdate,
)
from ..services import chat_session as chat_session_service
from ..services import displayed_files as displayed_files_service
from ..services import project as project_service
from ..services.chat_run import chat_run_registry, live_status
from ..services.displayed_files import DisplayedFileInfo


router = APIRouter(prefix="/projects", tags=["chat-sessions"])


def _public(row) -> ChatSessionPublic:
    public = ChatSessionPublic.model_validate(row, from_attributes=True)
    public.run_status = live_status(chat_run_registry.get(row.id)) or (
        row.run_state if row.run_unseen else "idle"
    )
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
    return _public(chat_session)


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
    return _public(chat_session)


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
    return _public(chat_session)


@router.delete("/{project_name}/chat-session", status_code=204)
async def delete_chat_session(
    project_name: str,
    session: SessionDep,
    chat_session_id: uuid.UUID,
) -> None:
    project = await project_service.get_project_by_name(session, project_name)
    # A run writes to the row it belongs to, so end it before the row goes.
    await chat_run_registry.stop(chat_session_id, "stopped")
    await chat_session_service.delete_chat_session(
        session,
        project_id=project.id,
        chat_session_id=chat_session_id,
    )


@router.get("/{project_name}/chat-sessions/{chat_session_id}/displayed-files")
async def list_displayed_files(
    project_name: str,
    chat_session_id: uuid.UUID,
    session: SessionDep,
) -> list[DisplayedFileInfo]:
    return await displayed_files_service.list_displayed_files(
        session, project_name, chat_session_id
    )
