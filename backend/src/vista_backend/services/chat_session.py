from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import ChatSessionTable, ChatSessionUpdate
from ..utils.misc import now_iso


_MESSAGE_HISTORY_ADAPTER = TypeAdapter(list[ModelMessage])


async def get_chat_session_optional(
    session: AsyncSession,
    *,
    project_id,
    user_id,
) -> ChatSessionTable | None:
    return (await session.exec(
        select(ChatSessionTable).where(
            ChatSessionTable.project_id == project_id,
            ChatSessionTable.user_id == user_id,
        )
    )).first()


async def get_or_create_chat_session(
    session: AsyncSession,
    *,
    project_id,
    user_id,
) -> ChatSessionTable:
    existing = await get_chat_session_optional(session, project_id=project_id, user_id=user_id)
    if existing is not None:
        return existing

    now = now_iso()
    row = ChatSessionTable(
        user_id=user_id,
        project_id=project_id,
        message_history=[],
        messages=[],
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def update_chat_session(
    session: AsyncSession,
    *,
    project_id,
    user_id,
    updates: ChatSessionUpdate,
) -> ChatSessionTable:
    row = await get_or_create_chat_session(session, project_id=project_id, user_id=user_id)
    normalized_history = _MESSAGE_HISTORY_ADAPTER.validate_python(updates.message_history)
    row.message_history = _MESSAGE_HISTORY_ADAPTER.dump_python(normalized_history, mode="json")
    row.messages = [message.model_dump(mode="json") for message in updates.messages]
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
    fallback_history: list[ModelMessage] | None = None,
) -> list[ModelMessage]:
    """
    Load the canonical persisted message history for a user/project.

    If the stored session is empty, fall back to caller-provided history so an
    older client can still continue a conversation during the migration to
    backend-owned state.
    """
    row = await get_or_create_chat_session(session, project_id=project_id, user_id=user_id)
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
) -> ChatSessionTable:
    row = await get_or_create_chat_session(session, project_id=project_id, user_id=user_id)
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
) -> ChatSessionTable:
    combined = [*prior_history, *new_messages]
    return await save_message_history(
        session,
        project_id=project_id,
        user_id=user_id,
        message_history=combined,
    )
