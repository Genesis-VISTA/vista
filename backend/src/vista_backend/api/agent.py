from typing import AsyncGenerator, Any

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, TypeAdapter
from pydantic_ai.messages import ModelMessage
from sse_starlette.sse import EventSourceResponse
from sse_starlette.event import ServerSentEvent
from ..agents.agents import ProjectAgentResult, McpElicitationEvent
from ..db.db import SessionDep
from ..db.schemas import ProjectPublic
from ..services import project as project_service
from ..services.project_agent import project_agent_pool, register_elicitation
from ..services.auth import UserDep

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
