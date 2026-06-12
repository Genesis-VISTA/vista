from fastapi import APIRouter

from ..db.db import SessionDep
from ..db.schemas import ChatSessionPublic, ChatSessionUpdate
from ..services import chat_session as chat_session_service
from ..services import project as project_service
from ..services.auth import UserDep


router = APIRouter(prefix="/projects", tags=["chat-sessions"])


@router.get("/{project_name}/chat-session")
async def get_chat_session(
    project_name: str,
    session: SessionDep,
    user: UserDep,
) -> ChatSessionPublic:
    project = await project_service.get_project_by_name(session, project_name, user)
    chat_session = await chat_session_service.get_or_create_chat_session(
        session,
        project_id=project.id,
        user_id=user.id,
    )
    return ChatSessionPublic.model_validate(chat_session)


@router.put("/{project_name}/chat-session")
async def put_chat_session(
    project_name: str,
    body: ChatSessionUpdate,
    session: SessionDep,
    user: UserDep,
) -> ChatSessionPublic:
    project = await project_service.get_project_by_name(session, project_name, user)
    chat_session = await chat_session_service.update_chat_session(
        session,
        project_id=project.id,
        user_id=user.id,
        updates=body,
    )
    return ChatSessionPublic.model_validate(chat_session)
