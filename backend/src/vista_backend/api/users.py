import uuid
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from ..agents.inference import PROVIDER_PRESETS, resolve_inference_target
from ..db.db import SessionDep
from ..db.schemas import (
    HpcCluster,
    UserCreate,
    UserUpdate,
    UserSelfUpdate,
    UserPublic,
    UserPublicWithConfig,
)
from ..services import globus_auth, hpc_status, user as user_service
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
    # A model chosen from one provider means nothing to another, and sending
    # it there fails confusingly. Cleared here rather than by the client so
    # every client agrees, and no second request can race the first. Compared
    # with the provider in effect, so choosing the one already in use keeps it.
    if (
        "inference_provider" in updates.model_fields_set
        and "inference_model" not in updates.model_fields_set
    ):
        current = resolve_inference_target(UserPublicWithConfig.model_validate(user))
        if updates.inference_provider != current.provider:
            updates.inference_model = None
    row = await user_service.update_user(session, user.id, updates, user)
    return UserPublicWithConfig.model_validate(row)


class InferenceProviderOption(BaseModel):
    id: str
    name: str
    takes_url: bool
    """ Only Custom asks for an endpoint; the others use their preset's. """
    default_model: str | None
    """ Bare model name used when none is chosen; `None` means one must be. """


class InferenceView(BaseModel):
    """
    What the interface needs to show the researcher's inference provider and
    model, without any secret: the picker reads this, not the full user.
    """

    providers: list[InferenceProviderOption]
    provider: str
    source: Literal["user", "config", "default"]
    """ The researcher's choice, the installation's configuration, or i2. """
    base_url: str
    model: str | None
    """ The model in effect, as `provider:name`; `None` when none is. """
    model_is_default: bool
    has_credential: bool
    """ Whether the provider in effect has a key, from any source. """
    keys_set: dict[str, bool]
    """ Whether the researcher saved a key for each provider. """


@router.get("/me/inference")
async def get_inference(user: UserDep) -> InferenceView:
    config = UserPublicWithConfig.model_validate(user)
    target = resolve_inference_target(config)
    return InferenceView(
        providers=[
            InferenceProviderOption(
                id=p.id,
                name=p.name,
                takes_url=p.takes_url,
                default_model=p.default_model,
            )
            for p in PROVIDER_PRESETS.values()
        ],
        provider=target.provider,
        source=target.source,
        base_url=target.base_url,
        model=target.model,
        model_is_default=target.model_is_default,
        has_credential=target.has_credential,
        keys_set={
            p.id: bool(getattr(config, p.key_field)) for p in PROVIDER_PRESETS.values()
        },
    )


@router.get("/me/hpc-status")
async def get_hpc_status(
    user: UserDep, fresh: bool = False, cluster: HpcCluster | None = None
) -> hpc_status.HpcStatus:
    """
    Whether each visible HPC cluster would work for the current user right
    now, from live checks against the facility, S3M, and Globus. Results are
    reused for a minute; `fresh=true` reruns them, for every cluster or, with
    `cluster`, for that one. Never carries a token.
    """
    return await hpc_status.hpc_status_service.status(
        user, fresh=fresh, cluster=cluster
    )


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
            {
                globus_auth.TOKEN_FIELDS[cluster]: connection.refresh_token,
                globus_auth.HTTPS_TOKEN_FIELDS[cluster]: connection.https_refresh_token,
            }
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
