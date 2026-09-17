import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from ..db.db import SessionDep
from ..db.schemas import (
    UserCreate,
    UserUpdate,
    UserSelfUpdate,
    UserPublic,
    UserPublicWithConfig,
)
from ..services import globus_auth, user as user_service
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


# ---------------------------------------------------------------------------
# Connecting Globus
#
# Two calls rather than a field on `PUT /me`, because this is an exchange and
# not a value: the researcher never sees the credential, and what they do paste
# is a single-use code that is worthless once spent.
# ---------------------------------------------------------------------------


class GlobusLoginStarted(BaseModel):
    authorize_url: str
    """Displayed for the researcher to open and, if the browser cannot be opened
    for them, to copy. Carries no secret: the verifier that makes the exchange
    work stays on the server."""


class GlobusLoginCode(BaseModel):
    code: str


class GlobusConnected(BaseModel):
    cluster: globus_auth.Cluster
    identity: str
    """Which Globus account this cluster is now connected as, so a researcher
    who authorizes both enclaves can see they landed where intended."""


@router.post("/me/globus/{cluster}/login")
async def start_globus_login(
    cluster: globus_auth.Cluster, user: UserDep
) -> GlobusLoginStarted:
    return GlobusLoginStarted(authorize_url=globus_auth.start_login(user.id, cluster))


@router.post("/me/globus/{cluster}/code")
async def complete_globus_login(
    cluster: globus_auth.Cluster,
    payload: GlobusLoginCode,
    session: SessionDep,
    user: UserDep,
) -> GlobusConnected:
    connection = globus_auth.complete_login(user.id, cluster, payload.code)
    await user_service.update_user(
        session,
        user.id,
        UserSelfUpdate.model_validate(
            {globus_auth.TOKEN_FIELDS[cluster]: connection.refresh_token}
        ),
        user,
    )
    return GlobusConnected(cluster=cluster, identity=connection.identity)


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
