"""
VISTAGuard trust-state + re-auth endpoints (Phase 5).

Mounted on the app only when `settings.vistaguard.enabled` is true (see
`api.api`), so the endpoints -- and their OpenAPI schemas -- are absent
entirely when VISTAGuard is disabled.
"""

from pydantic import BaseModel

from ..config import settings
from ..db.db import SessionDep
from ..db.schemas import ProjectPublic
from ..services import project as project_service
from ..services.project_agent import find_live_project_agent_key, project_agent_pool
from ..services.auth import UserDep
from ..vistaguard import TrustScorer
from fastapi import APIRouter

router = APIRouter(tags=["vistaguard"])


class VistaGuardCapabilityState(BaseModel):
    """Trust state for a single capability kind (e.g. a gate ``"G2"``)."""
    score: float
    """Posterior-mean trust for this capability, in [0, 1]."""
    tier: str
    """The capability's current tier (never better than its sticky floor)."""
    clean_calls: int
    violations: int
    sticky: bool
    """True once a high-stakes violation has locked this capability in."""
    floor: str | None
    """The sticky floor tier this capability cannot climb above, or null."""


class VistaGuardStateResponse(BaseModel):
    """Per-capability trust scores and tiers for the caller's session."""
    score: float
    """Pooled trust score across all capabilities, in [0, 1]."""
    tier: str
    """Session tier (the worst capability's tier)."""
    terminated: bool
    """True once a SEV1 incident has terminated the session."""
    reauth_required: bool
    """True once a SEV2 incident has forced re-authentication."""
    violations: int
    sticky_denied: list[str]
    """Capability keys sticky-denied this session (cleared by re-auth)."""
    contract_coverage: dict
    """Domain-contract coverage: claims checked/covered, coverage fraction,
    and contract violations seen this session."""
    capabilities: dict[str, VistaGuardCapabilityState]


class VistaGuardReauthResponse(BaseModel):
    """Result of clearing sticky high-stakes lock-in after a user re-auth."""
    unlocked_capabilities: list[str]
    """Capability kinds whose sticky lock was lifted by this re-auth."""


@router.get(
    "/projects/{project_name}/vistaguard/state",
    response_model=VistaGuardStateResponse,
)
async def vistaguard_state(
    project_name: str, session: SessionDep, user: UserDep,
) -> VistaGuardStateResponse:
    """
    Return the current VISTAGuard trust state for the caller's session:
    the pooled score and session tier, plus the per-capability score and
    tier for every capability that has recorded a signal this session.

    Requires the same project authorization as every other
    `/projects/{project_name}/...` route. When the caller has no live
    agent session, the initial (all-NORMAL) state is returned rather
    than spinning one up.
    """
    project_row = await project_service.get_project_by_name(session, project_name, user)
    project = ProjectPublic.model_validate(project_row)

    key = await find_live_project_agent_key(session, project_id=project.id, user_id=user.id)
    if key is not None:
        async with project_agent_pool.get(key) as agent:
            snapshot = agent.sidecar.trust_scorer.snapshot()
    else:
        # No live session: report the fresh trust state without building
        # an agent (which would start MCP servers as a side effect).
        snapshot = TrustScorer(settings.vistaguard).snapshot()

    return VistaGuardStateResponse.model_validate(snapshot)


@router.post(
    "/projects/{project_name}/vistaguard/reauth",
    response_model=VistaGuardReauthResponse,
)
async def vistaguard_reauth(
    project_name: str, session: SessionDep, user: UserDep,
) -> VistaGuardReauthResponse:
    """
    Clear sticky high-stakes lock-in for the caller's session after a
    user re-authentication flow (proposal §8.4 / C4). Previously
    locked high-stakes capabilities become usable again.

    Authorization piggybacks on the existing project authorization --
    a caller who can run the agent for this project can re-auth its
    trust state; there is no separate re-auth credential.
    """
    project_row = await project_service.get_project_by_name(session, project_name, user)
    project = ProjectPublic.model_validate(project_row)

    key = await find_live_project_agent_key(session, project_id=project.id, user_id=user.id)
    unlocked: tuple[str, ...] = ()
    if key is not None:
        async with project_agent_pool.get(key) as agent:
            unlocked = agent.sidecar.trust_scorer.reauthenticate()

    return VistaGuardReauthResponse(unlocked_capabilities=list(unlocked))
