from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import ChatSessionTable, ChatSessionUpdate
from ..utils.misc import now_iso


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
    row.message_history = updates.message_history
    row.messages = [message.model_dump(mode="json") for message in updates.messages]
    row.updated_at = now_iso()
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row
