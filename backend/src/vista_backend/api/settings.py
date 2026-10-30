from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from ..agents.inference import PROVIDER_PRESETS, resolve_inference_target
from ..db.db import SessionDep
from ..db.schemas import HpcCluster, LocalSettingsPublic, LocalSettingsUpdate
from ..services import globus_auth, hpc_status, local_settings
from ..services.local_settings import LocalSettingsDep

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def get_settings(local: LocalSettingsDep) -> LocalSettingsPublic:
    """The installation's settings, including decrypted credentials."""
    return local


@router.put("")
async def update_settings(
    updates: LocalSettingsUpdate, session: SessionDep, local: LocalSettingsDep
) -> LocalSettingsPublic:
    # A model chosen from one provider means nothing to another, and sending
    # it there fails confusingly. Cleared here rather than by the client so
    # every client agrees, and no second request can race the first. Compared
    # with the provider in effect, so choosing the one already in use keeps it.
    if (
        "inference_provider" in updates.model_fields_set
        and "inference_model" not in updates.model_fields_set
    ):
        current = resolve_inference_target(local)
        if updates.inference_provider != current.provider:
            updates.inference_model = None
    row = await local_settings.update(session, updates)
    return local_settings.to_public(row)


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
    model, without any secret: the picker reads this, not the full settings.
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


@router.get("/inference")
async def get_inference(local: LocalSettingsDep) -> InferenceView:
    target = resolve_inference_target(local)
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
            p.id: bool(getattr(local, p.key_field)) for p in PROVIDER_PRESETS.values()
        },
    )


@router.get("/hpc-status")
async def get_hpc_status(
    local: LocalSettingsDep, fresh: bool = False, cluster: HpcCluster | None = None
) -> hpc_status.HpcStatus:
    """
    Whether each visible HPC cluster would work right now, from live checks against the facility, S3M, and Globus. Results are
    reused for a minute; `fresh=true` reruns them, for every cluster or, with
    `cluster`, for that one. Never carries a token.
    """
    return await hpc_status.hpc_status_service.status(
        local, fresh=fresh, cluster=cluster
    )


# ---------------------------------------------------------------------------
# Connecting Globus
#
# Two calls rather than a field on `PUT /settings`, because this is an exchange and
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


@router.post("/globus/{cluster}/login")
async def start_globus_login(cluster: globus_auth.Cluster) -> GlobusLoginStarted:
    return GlobusLoginStarted(authorize_url=globus_auth.start_login(cluster))


@router.post("/globus/{cluster}/code")
async def complete_globus_login(
    cluster: globus_auth.Cluster,
    payload: GlobusLoginCode,
    session: SessionDep,
) -> GlobusConnected:
    connection = globus_auth.complete_login(cluster, payload.code)
    await local_settings.update(
        session,
        LocalSettingsUpdate.model_validate(
            {
                globus_auth.TOKEN_FIELDS[cluster]: connection.refresh_token,
                globus_auth.HTTPS_TOKEN_FIELDS[cluster]: connection.https_refresh_token,
            }
        ),
    )
    return GlobusConnected(cluster=cluster, identity=connection.identity)
