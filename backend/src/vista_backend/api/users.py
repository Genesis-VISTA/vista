import uuid

from fastapi import APIRouter

from ..db.db import SessionDep
from ..db.schemas import (
    UserCreate,
    UserUpdate,
    UserSelfUpdate,
    UserPublic,
    UserPublicWithConfig,
)
from ..services import user as user_service
from ..services.auth import AdminDep, UserDep

router = APIRouter(prefix="/users", tags=["users"])

# TODO: Should handle secrets better


@router.get("/me")
async def get_me(
    user: UserDep, config: bool = False
) -> UserPublicWithConfig | UserPublic:
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
    row = await user_service.update_user(session, user.id, updates, user)
    return UserPublicWithConfig.model_validate(row)


@router.get("")
async def list_users(session: SessionDep, user: AdminDep) -> list[UserPublic]:
    users = await user_service.list_users(session, user)
    return [UserPublic.model_validate(u) for u in users]


@router.get("/{user_id}")
async def get_user(
    user_id: uuid.UUID, session: SessionDep, user: AdminDep
) -> UserPublic:
    existing_user = await user_service.get_user_by_id(session, user_id, user)
    return UserPublic.model_validate(existing_user)


@router.post("", status_code=201)
async def create_user(
    payload: UserCreate, session: SessionDep, user: AdminDep
) -> UserPublic:
    new = await user_service.create_user(session, payload, user)
    return UserPublic.model_validate(new)


@router.put("/{user_id}")
async def update_user(
    user_id: uuid.UUID, updates: UserUpdate, session: SessionDep, user: AdminDep
) -> UserPublic:
    existing_user = await user_service.update_user(session, user_id, updates, user)
    return UserPublic.model_validate(existing_user)


@router.delete("/{user_id}", status_code=204)
async def delete_user(user_id: uuid.UUID, session: SessionDep, user: AdminDep) -> None:
    await user_service.delete_user(session, user_id, user)
