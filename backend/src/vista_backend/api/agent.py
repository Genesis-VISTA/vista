from typing import AsyncGenerator, Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import Response
from pydantic import BaseModel, TypeAdapter
from pydantic_ai.messages import ModelMessage
from sse_starlette.sse import EventSourceResponse
from sse_starlette.event import ServerSentEvent
from ..agents.agents import ProjectAgentResult, McpElicitationEvent
from ..config import settings
from ..db.db import SessionDep
from ..db.schemas import ProjectPublic
from ..services import project as project_service
from ..services.project_agent import project_agent_pool, register_elicitation
from ..vistaguard import TrustScorer
from ..vistaguard.config import VistaGuardSettings
from .auth import UserDep

router = APIRouter()


class AgentRunRequest(BaseModel):
    """
    Request body for an agent turn.

    `message_history` and the returned `new_messages` use PydanticAI's
    `ModelMessage` schema -- see https://pydantic.dev/docs/ai/core-concepts/messages/
    for the message/part shape. To continue a conversation, append the previous
    call's `new_messages` to your stored history and send the result here.
    """
    stream: bool = False
    """ If True, stream the response as Server-Sent Events. """
    user_prompt: str
    """ The user prompt to the agent. """
    message_history: list[ModelMessage] = []
    """ Prior `ModelMessage`s from earlier turns (as returned from a previous call). """

@router.post("/projects/{project_name}/agent/run", response_model=ProjectAgentResult)
async def agent_run(
    project_name: str, body: AgentRunRequest, session: SessionDep, user: UserDep,
) -> ProjectAgentResult | Response:
    """
    Stateless chat completion that runs the full agent loop for one turn.

    Pass the `message_history`. The response contains the messages produced in this agent turn --
    append them to your stored history to continue in the next call.

    Both the streaming and non-streaming payloads are built directly on PydanticAI's data model:

    The `ProjectAgentResult` from non streaming and the final streaming event contains `new_messages`,
    which is a list of `pydantic_ai.messages.ModelMessage`, and `usage` is `pydantic_ai.RunUsage`,
    and `logs` is the list of `LogEvent`s emitted during the run.

    In streaming, each event corresponds to PydanticAI's `pydantic_ai.messages.AgentStreamEvent` see
    https://pydantic.dev/docs/ai/core-concepts/agent/#running-agents
    In addition to Pydantic's event's we also yield LogEvents from the server and mcp servers.
    The final event in streaming contains a `ProjectAgentResult`, same as the result from non streaming.

    Supports MCP elicitation in streaming mode. Elicitation requests arrive as an extra SSE event
    interleaved with the agent events:
    - event: mcp_form_elicitation  data: {"elicitation_id": "<id>", "mode": "form", "message": "...", "requested_schema": {...}}
    - event: mcp_url_elicitation   data: {"elicitation_id": "<id>", "mode": "url", "message": "...", "url": "https://..."}
    The client must POST the response to /projects/{project_name}/elicitation. For URL mode, "accept" means the
    user consented to navigate to the URL; the out-of-band interaction completes separately.
    """
    project_row = await project_service.get_project_by_name(session, project_name, user)
    project = ProjectPublic.model_validate(project_row)

    if body.stream:
        async def agent_events() -> AsyncGenerator[ServerSentEvent, None]:
            async with project_agent_pool.get((project.id, user.id)) as agent:
                async for event in agent.run_stream(
                    user_prompt=body.user_prompt,
                    message_history=body.message_history,
                    enable_elicitation=True,
                ):
                    if isinstance(event, McpElicitationEvent):
                        register_elicitation(event.elicitation_id, agent)
                        # calling /projects/{project_name}/elicitation will resolve the elicitation request
                    data = TypeAdapter(Any).dump_json(event).decode()
                    yield ServerSentEvent(event=event.event_kind, data=data)

        return EventSourceResponse(agent_events())
    else:
        async with project_agent_pool.get((project.id, user.id)) as agent:
            return await agent.run(
                user_prompt=body.user_prompt,
                message_history=body.message_history,
            )


# -----------------------------------------------------------------
# VISTAGuard trust-state + re-auth endpoints (Phase 5)
#
# These live on a dedicated router that is only mounted when the
# VISTAGuard master flag is on (see `register_vistaguard_routes`), so
# the endpoints -- and their OpenAPI schemas -- are absent entirely
# when `vistaguard.enabled=false`.
# -----------------------------------------------------------------


vistaguard_router = APIRouter(tags=["vistaguard"])


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


@vistaguard_router.get(
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

    key = (project.id, user.id)
    if key in project_agent_pool.keys():
        async with project_agent_pool.get(key) as agent:
            snapshot = agent.sidecar.trust_scorer.snapshot()
    else:
        # No live session: report the fresh trust state without building
        # an agent (which would start MCP servers as a side effect).
        snapshot = TrustScorer(settings.vistaguard).snapshot()

    return VistaGuardStateResponse.model_validate(snapshot)


@vistaguard_router.post(
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

    key = (project.id, user.id)
    unlocked: tuple[str, ...] = ()
    if key in project_agent_pool.keys():
        async with project_agent_pool.get(key) as agent:
            unlocked = agent.sidecar.trust_scorer.reauthenticate()

    return VistaGuardReauthResponse(unlocked_capabilities=list(unlocked))


def register_vistaguard_routes(app: FastAPI, vg_settings: VistaGuardSettings) -> bool:
    """
    Mount the VISTAGuard state/re-auth router on `app` iff VISTAGuard is
    enabled. Returns whether the routes were mounted.

    Gating registration (rather than 404-ing inside the handlers) keeps
    the endpoints out of the OpenAPI schema entirely when
    `vistaguard.enabled=false`, satisfying the "endpoints absent when
    disabled" requirement.
    """
    if not vg_settings.enabled:
        return False
    app.include_router(vistaguard_router)
    return True
