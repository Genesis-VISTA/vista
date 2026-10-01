import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage
from sse_starlette.sse import EventSourceResponse
from sse_starlette.event import ServerSentEvent
from ..agents.agents import ProjectAgentResult
from ..db.db import SessionDep
from ..db.schemas import ProjectPublic
from ..services import chat_session as chat_session_service
from ..services import project as project_service
from ..services.chat_run import RunBusy, chat_run_registry
from ..services.project_agent import get_project_agent_key, project_agent_pool
from ..agents.inference import require_inference_credential
from ..services.auth import UserDep

router = APIRouter()


class AgentRunRequest(BaseModel):
    """
    Request body for an agent turn.

    `message_history` is kept as a compatibility fallback while session-backed
    history ownership moves to the backend. `new_messages` still uses
    PydanticAI's `ModelMessage` schema -- see
    https://pydantic.dev/docs/ai/core-concepts/messages/ for the message/part
    shape.
    """

    stream: bool = False
    """ If True, stream the response as Server-Sent Events. Requires `chat_session_id`. """
    user_prompt: str
    """ The user prompt to the agent. """
    chat_session_id: uuid.UUID | None = None
    """
    The conversation this turn belongs to. A turn in a conversation runs in the
    background and finishes whether or not anyone is watching. Without it, a
    non-streaming request is a stateless one-off that is saved nowhere.
    """
    message_history: list[ModelMessage] = Field(default_factory=list)
    """ Optional fallback history from older clients; backend session state wins when present. """


@router.post("/projects/{project_name}/agent/run", response_model=ProjectAgentResult)
async def agent_run(
    project_name: str,
    body: AgentRunRequest,
    session: SessionDep,
    user: UserDep,
) -> ProjectAgentResult | Response:
    """
    Session-backed chat completion that runs the full agent loop for one turn.

    Both the streaming and non-streaming payloads are built directly on PydanticAI's data model:

    The `ProjectAgentResult` from non streaming and the final streaming event contains `new_messages`,
    which is a list of `pydantic_ai.messages.ModelMessage`, and `usage` is `pydantic_ai.RunUsage`,
    and `logs` is the list of `LogEvent`s emitted during the run.

    In streaming, each event corresponds to PydanticAI's `pydantic_ai.messages.AgentStreamEvent` see
    https://pydantic.dev/docs/ai/core-concepts/agent/#running-agents
    In addition to Pydantic's event's we also yield LogEvents from the server and mcp servers.
    The final event in streaming contains a `ProjectAgentResult`, same as the result from non streaming.

    A turn with a `chat_session_id` is owned by the backend (`services/chat_run.py`): the stream is
    only a watcher. If the client disconnects the turn keeps running and saves its own result; watch it
    again with GET /projects/{project_name}/chat-sessions/{id}/run/events, or cancel it with POST .../run/stop.
    Each stream starts with a `run_started` event and ends with `run_finished {state}`. A second turn in a
    conversation that is still running is refused with 409.

    Supports MCP elicitation in streaming mode. Elicitation requests arrive as an extra SSE event
    interleaved with the agent events:
    - event: mcp_form_elicitation  data: {"elicitation_id": "<id>", "mode": "form", "message": "...", "requested_schema": {...}}
    - event: mcp_url_elicitation   data: {"elicitation_id": "<id>", "mode": "url", "message": "...", "url": "https://..."}
    The client must POST the response to /projects/{project_name}/elicitation. For URL mode, "accept" means the
    user consented to navigate to the URL; the out-of-band interaction completes separately.
    """
    # Checked before either branch: in the streaming case the response status
    # is committed the moment `EventSourceResponse` is returned, so a raise
    # from inside the generator could only surface as a truncated stream.
    require_inference_credential(user)

    project_row = await project_service.get_project_by_name(session, project_name, user)
    project = ProjectPublic.model_validate(project_row)
    agent_key = await get_project_agent_key(
        session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=body.chat_session_id,
    )
    effective_history = await chat_session_service.get_effective_message_history(
        session,
        project_id=project.id,
        user_id=user.id,
        fallback_history=body.message_history,
        chat_session_id=body.chat_session_id,
    )

    if body.stream and body.chat_session_id is None:
        raise HTTPException(
            status_code=400, detail="chat_session_id is required to stream a turn"
        )

    if body.chat_session_id is not None:
        # The run writes through its own session, so end this request's
        # transaction first rather than hold a lock it could wait on.
        await session.commit()
        try:
            run = await chat_run_registry.start(
                chat_session_id=body.chat_session_id,
                project_id=project.id,
                user_id=user.id,
                agent_key=agent_key,
                user_prompt=body.user_prompt,
                prior_history=effective_history,
            )
        except RunBusy:
            raise HTTPException(
                status_code=409,
                detail="This conversation already has a turn running.",
            )

        if body.stream:

            async def watch() -> AsyncGenerator[ServerSentEvent, None]:
                async for event in run.subscribe():
                    yield ServerSentEvent(
                        event=event.kind, data=event.data, id=str(event.seq)
                    )

            return EventSourceResponse(watch())

        await run.wait()
        if run.result is None:
            raise HTTPException(status_code=500, detail=f"Turn {run.state}.")
        return run.result

    async with project_agent_pool.get(agent_key) as agent:
        return await agent.run(
            user_prompt=body.user_prompt,
            message_history=effective_history,
        )
