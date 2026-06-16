import uuid

from fastapi import HTTPException
from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage
from sqlmodel import desc, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import ChatSessionCreate, ChatSessionTable, ChatSessionUpdate
from ..utils.misc import now_iso


_MESSAGE_HISTORY_ADAPTER = TypeAdapter(list[ModelMessage])


async def list_chat_sessions(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[ChatSessionTable]:
    stmt = (
        select(ChatSessionTable)
        .where(
            ChatSessionTable.project_id == project_id,
            ChatSessionTable.user_id == user_id,
        )
        .order_by(desc(ChatSessionTable.updated_at), desc(ChatSessionTable.created_at))
    )
    return list((await session.exec(stmt)).all())


async def get_chat_session_optional(
    session: AsyncSession,
    *,
    project_id,
    user_id,
    chat_session_id: uuid.UUID | None = None,
) -> ChatSessionTable | None:
    stmt = select(ChatSessionTable).where(
            ChatSessionTable.project_id == project_id,
            ChatSessionTable.user_id == user_id,
        )
    if chat_session_id is not None:
        stmt = stmt.where(ChatSessionTable.id == chat_session_id)
    else:
        stmt = stmt.order_by(desc(ChatSessionTable.updated_at), desc(ChatSessionTable.created_at))
    return (await session.exec(stmt)).first()


async def get_chat_session(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    chat_session_id: uuid.UUID,
) -> ChatSessionTable:
    row = await get_chat_session_optional(
        session,
        project_id=project_id,
        user_id=user_id,
        chat_session_id=chat_session_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return row


def _default_chat_title(existing_count: int) -> str:
    return f"Conversation {existing_count + 1}"


async def create_chat_session(
    session: AsyncSession,
    *,
    project_id,
    user_id,
    payload: ChatSessionCreate | None = None,
) -> ChatSessionTable:
    existing = await list_chat_sessions(session, project_id=project_id, user_id=user_id)
    now = now_iso()
    row = ChatSessionTable(
        user_id=user_id,
        project_id=project_id,
        title=(
            payload.title.strip()
            if payload and payload.title and payload.title.strip()
            else _default_chat_title(len(existing))
        ),
        message_history=[],
        messages=[],
        latest_result=None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def get_or_create_chat_session(
    session: AsyncSession,
    *,
    project_id,
    user_id,
    chat_session_id: uuid.UUID | None = None,
) -> ChatSessionTable:
    if chat_session_id is not None:
        return await get_chat_session(
            session,
            project_id=project_id,
            user_id=user_id,
            chat_session_id=chat_session_id,
        )

    existing = await get_chat_session_optional(session, project_id=project_id, user_id=user_id)
    if existing is not None:
        return existing

    return await create_chat_session(session, project_id=project_id, user_id=user_id)


async def update_chat_session(
    session: AsyncSession,
    *,
    project_id,
    user_id,
    updates: ChatSessionUpdate,
    chat_session_id: uuid.UUID | None = None,
) -> ChatSessionTable:
    row = await get_or_create_chat_session(
        session,
        project_id=project_id,
        user_id=user_id,
        chat_session_id=chat_session_id,
    )
    normalized_history = _MESSAGE_HISTORY_ADAPTER.validate_python(updates.message_history)
    row.message_history = _MESSAGE_HISTORY_ADAPTER.dump_python(normalized_history, mode="json")
    row.messages = [message.model_dump(mode="json") for message in updates.messages]
    row.latest_result = updates.latest_result
    if updates.title is not None and updates.title.strip():
        row.title = updates.title.strip()
    row.updated_at = now_iso()
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def get_effective_message_history(
    session: AsyncSession,
    *,
    project_id,
    user_id,
    chat_session_id: uuid.UUID | None = None,
    fallback_history: list[ModelMessage] | None = None,
) -> list[ModelMessage]:
    """
    Load the canonical persisted message history for a user/project.

    If the stored session is empty, fall back to caller-provided history so an
    older client can still continue a conversation during the migration to
    backend-owned state.
    """
    row = await get_or_create_chat_session(
        session,
        project_id=project_id,
        user_id=user_id,
        chat_session_id=chat_session_id,
    )
    persisted = _MESSAGE_HISTORY_ADAPTER.validate_python(row.message_history or [])
    if persisted:
        return persisted
    return _MESSAGE_HISTORY_ADAPTER.validate_python(fallback_history or [])


async def save_message_history(
    session: AsyncSession,
    *,
    project_id,
    user_id,
    message_history: list[ModelMessage],
    chat_session_id: uuid.UUID | None = None,
) -> ChatSessionTable:
    row = await get_or_create_chat_session(
        session,
        project_id=project_id,
        user_id=user_id,
        chat_session_id=chat_session_id,
    )
    normalized_history = _MESSAGE_HISTORY_ADAPTER.validate_python(message_history)
    row.message_history = _MESSAGE_HISTORY_ADAPTER.dump_python(normalized_history, mode="json")
    row.updated_at = now_iso()
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def append_message_history(
    session: AsyncSession,
    *,
    project_id,
    user_id,
    prior_history: list[ModelMessage],
    new_messages: list[ModelMessage],
    chat_session_id: uuid.UUID | None = None,
) -> ChatSessionTable:
    combined = [*prior_history, *new_messages]
    return await save_message_history(
        session,
        project_id=project_id,
        user_id=user_id,
        chat_session_id=chat_session_id,
        message_history=combined,
    )
