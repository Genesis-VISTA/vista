import uuid

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import UserCreate, UserTable, UserUpdate, UserSelfUpdate
from .project_agent import invalidate_agents
from ._helpers import ServiceUser


def _require_admin(user: ServiceUser) -> None:
    """Raise 403 unless `user` is admin. `user="system"` bypasses the check."""
    if user != "system" and not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")


def _require_self_or_admin(user: ServiceUser, target_id: uuid.UUID) -> None:
    """Raise 403 unless `user` is admin or is `target_id`. `user="system"` bypasses the check."""
    if user != "system" and not user.is_admin and user.id != target_id:
        raise HTTPException(status_code=403, detail="You can only access your own user")


async def list_users(session: AsyncSession, user: ServiceUser) -> list[UserTable]:
    _require_admin(user)
    return list((await session.exec(select(UserTable))).all())


async def get_user_by_id_optional(session: AsyncSession, user_id: uuid.UUID, user: ServiceUser) -> UserTable | None:
    _require_self_or_admin(user, user_id)
    return (await session.exec(select(UserTable).where(UserTable.id == user_id))).first()


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID, user: ServiceUser) -> UserTable:
    existing = await get_user_by_id_optional(session, user_id, user)
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")
    return existing


async def get_user_by_email_optional(session: AsyncSession, email: str, user: ServiceUser) -> UserTable | None:
    if user != "system" and not user.is_admin and user.email != email:
        raise HTTPException(status_code=403, detail="You can only access your own user")
    return (await session.exec(select(UserTable).where(UserTable.email == email))).first()


async def get_user_by_email(session: AsyncSession, email: str, user: ServiceUser) -> UserTable:
    existing = await get_user_by_email_optional(session, email, user)
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")
    return existing


async def create_user(session: AsyncSession, payload: UserCreate, user: ServiceUser) -> UserTable:
    _require_admin(user)
    new = UserTable.model_validate(payload)
    session.add(new)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"A user with email {payload.email!r} already exists.")
    await session.refresh(new)
    return new


async def update_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    updates: UserUpdate | UserSelfUpdate,
    user: ServiceUser,
) -> UserTable:
    _require_self_or_admin(user, user_id)
    existing = await get_user_by_id(session, user_id, user)
    for key, value in updates.model_dump(exclude_unset=True).items():
        setattr(existing, key, value)
    session.add(existing)
    await session.flush()
    await session.refresh(existing)
    invalidate_agents(session, user_id=existing.id)
    return existing


async def delete_user(session: AsyncSession, user_id: uuid.UUID, user: ServiceUser) -> None:
    _require_admin(user)
    existing = await get_user_by_id(session, user_id, user)
    await session.delete(existing)
    invalidate_agents(session, user_id=user_id)
