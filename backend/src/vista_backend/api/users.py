import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..db.db import SessionDep
from ..db.schemas import UserCreate, UserUpdate, UserSelfUpdate, UserPublic, UserPublicWithConfig, UserTable
from .auth import AdminDep, UserDep

router = APIRouter(prefix="/users", tags=["users"])

# TODO: Should handle secrets better

@router.get("/me")
async def get_me(user: UserDep, config: bool = False) -> UserPublicWithConfig | UserPublic:
    """
    Return the current user. Pass `?config=true` to include the per-user
    config (HPC dir, NERSC account, decrypted tokens);
    """
    if config:
        return UserPublicWithConfig.model_validate(user)
    else:
        return UserPublic.model_validate(user)


@router.put("/me")
async def update_me(
    updates: UserSelfUpdate, session: SessionDep, user: UserDep
) -> UserPublicWithConfig:
    for key, value in updates.model_dump(exclude_unset=True).items():
        setattr(user, key, value)
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return UserPublicWithConfig.model_validate(user)


@router.get("")
async def list_users(session: SessionDep, user: AdminDep) -> list[UserPublic]:
    users = (await session.exec(select(UserTable))).all()
    return [UserPublic.model_validate(u) for u in users]


@router.get("/{user_id}")
async def get_user(user_id: uuid.UUID, session: SessionDep, user: AdminDep) -> UserPublic:
    existing_user = (await session.exec(select(UserTable).where(UserTable.id == user_id))).first()
    if existing_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserPublic.model_validate(existing_user)


@router.post("", status_code=201)
async def create_user(payload: UserCreate, session: SessionDep, user: AdminDep) -> UserPublic:
    new = UserTable.model_validate(payload)
    session.add(new)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"A user with email {payload.email!r} already exists.")
    await session.refresh(new)
    return UserPublic.model_validate(new)


@router.put("/{user_id}")
async def update_user(
    user_id: uuid.UUID, updates: UserUpdate, session: SessionDep, user: AdminDep
) -> UserPublic:
    existing_user = (await session.exec(select(UserTable).where(UserTable.id == user_id))).first()
    if existing_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    for key, value in updates.model_dump(exclude_unset=True).items():
        setattr(existing_user, key, value)
    session.add(existing_user)
    await session.flush()
    await session.refresh(existing_user)
    return UserPublic.model_validate(existing_user)


@router.delete("/{user_id}", status_code=204)
async def delete_user(user_id: uuid.UUID, session: SessionDep, user: AdminDep) -> None:
    existing_user = (await session.exec(select(UserTable).where(UserTable.id == user_id))).first()
    if existing_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    await session.delete(existing_user)
    await session.flush()
