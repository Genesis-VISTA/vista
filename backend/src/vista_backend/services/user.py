import uuid

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import UserCreate, UserTable, UserUpdate, UserSelfUpdate
from .project_agent import invalidate_agents


async def list_users(session: AsyncSession) -> list[UserTable]:
    return list((await session.exec(select(UserTable))).all())


async def get_user_by_id_optional(session: AsyncSession, user_id: uuid.UUID) -> UserTable | None:
    return (await session.exec(select(UserTable).where(UserTable.id == user_id))).first()


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> UserTable:
    user = await get_user_by_id_optional(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def get_user_by_email_optional(session: AsyncSession, email: str) -> UserTable | None:
    return (await session.exec(select(UserTable).where(UserTable.email == email))).first()


async def get_user_by_email(session: AsyncSession, email: str) -> UserTable:
    user = await get_user_by_email_optional(session, email)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def create_user(session: AsyncSession, payload: UserCreate) -> UserTable:
    new = UserTable.model_validate(payload)
    session.add(new)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"A user with email {payload.email!r} already exists.")
    await session.refresh(new)
    return new


async def update_user(session: AsyncSession, user_id: uuid.UUID, updates: UserUpdate|UserSelfUpdate) -> UserTable:
    user = await get_user_by_id(session, user_id)
    for key, value in updates.model_dump(exclude_unset=True).items():
        setattr(user, key, value)
    session.add(user)
    await session.flush()
    await session.refresh(user)
    invalidate_agents(session, user_id=user.id)
    return user


async def delete_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    user = await get_user_by_id(session, user_id)
    await session.delete(user)
    invalidate_agents(session, user_id=user_id)
