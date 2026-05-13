import uuid
from typing import AsyncGenerator, Any
import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, TypeAdapter
from pydantic_ai import AgentRunResultEvent, RunUsage
from pydantic_ai.messages import ModelMessage
from mcp.client.session import ClientSession
from mcp.shared.context import RequestContext
import mcp.types
from sqlmodel import select
from sse_starlette.sse import EventSourceResponse
from sse_starlette.event import ServerSentEvent
from ..agents.agents import ProjectAgent
from ..db.db import SessionDep
from ..db.schemas import ProjectPublic, ProjectTable

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

class AgentRunResponse(BaseModel):
    """
    Response body for a non-streaming agent turn.

    `new_messages` is a list of PydanticAI `ModelMessage` objects produced during this
    run -- model requests, tool calls, tool returns, and the final text response. Append
    them to your stored `message_history` to continue the conversation on the next call.

    See https://pydantic.dev/docs/ai/core-concepts/agent/#running-agents for the agent
    run model and https://pydantic.dev/docs/ai/core-concepts/messages/ for the message
    schema.
    """
    new_messages: list[ModelMessage]
    """ Messages produced during this run; append to `message_history` for the next call. """
    usage: RunUsage
    """ Token / request usage for this run -- see `pydantic_ai.RunUsage`. """

@router.post("/projects/{project_name}/agent/run", response_model=AgentRunResponse)
async def agent_run(
    project_name: str, body: AgentRunRequest, session: SessionDep, request: Request,
) -> AgentRunResponse | Response:
    """
    Stateless chat completion that runs the full agent loop for one turn.

    Pass the `message_history`. The response contains the messages produced in this agent turn --
    append them to your stored history to continue in the next call.

    Both the streaming and non-streaming payloads are built directly on PydanticAI's data model:

    - Non-streaming response: see `AgentRunResponse` -- `new_messages` is a list of
      `pydantic_ai.messages.ModelMessage`, `usage` is `pydantic_ai.RunUsage`.

    - Streaming response: each SSE event corresponds to one PydanticAI `AgentStreamEvent` (e.g. 
      `part_start`, `part_delta`, `part_end`, `function_tool_call`, `function_tool_result`, 
      `final_result`) plus a terminal `agent_run_result` event. The SSE `event:` field is the 
      event's `event_kind` discriminator, and the `data:` payload is the JSON-serialized event 
      body. See `pydantic_ai.messages.AgentStreamEvent` and `pydantic_ai.AgentRunResultEvent`, 
      and the PydanticAI run docs at https://pydantic.dev/docs/ai/core-concepts/agent/#running-agents

      The `agent_run_result` event carries `{new_messages, usage}` -- the same shape
      `AgentRunResponse` returns in non-streaming mode.

    Supports MCP elicitation in streaming mode. Elicitation requests arrive as an extra SSE event
    interleaved with the agent events:
    - event: mcp_elicitation  data: {"elicitationId": "<id>", "mode": "form", "message": "...", "requestedSchema": {...}}
    - event: mcp_elicitation  data: {"elicitationId": "<id>", "mode": "url", "message": "...", "url": "https://..."}
    The client must POST the response to /mcp/elicitation. For URL mode, "accept" means the
    user consented to navigate to the URL; the out-of-band interaction completes separately.
    """
    project = (await session.exec(
        select(ProjectTable).where(ProjectTable.name == project_name)
    )).first()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    project = ProjectPublic.model_validate(project)
    agent = ProjectAgent(project)

    if body.stream:
        # We have to do some custom async queue to handle interleaving the MCP Elicitation events
        # and the primary agent stream events.
        queue: asyncio.Queue[ServerSentEvent | None] = asyncio.Queue()

        async def handle_elicitation(
            context: RequestContext[ClientSession, Any, Any],
            params: mcp.types.ElicitRequestParams,
        ) -> mcp.types.ElicitResult:
            if isinstance(params, mcp.types.ElicitRequestFormParams):
                elicitation_id = str(uuid.uuid4())
                event_data: dict[str, Any] = {
                    "elicitationId": elicitation_id,
                    "mode": params.mode,
                    "message": params.message,
                    "requestedSchema": params.requestedSchema,
                }
            elif isinstance(params, mcp.types.ElicitRequestURLParams):
                elicitation_id = params.elicitationId
                event_data = {
                    "elicitationId": elicitation_id,
                    "mode": params.mode,
                    "message": params.message,
                    "url": params.url,
                }
            else:
                return mcp.types.ElicitResult(action="cancel")

            # URL-mode ids come from the upstream server; refuse a duplicate sent by the MCP server
            if elicitation_id in request.app.state.elicitations:
                return mcp.types.ElicitResult(action="cancel")

            future: asyncio.Future[mcp.types.ElicitResult] = asyncio.get_running_loop().create_future()
            request.app.state.elicitations[elicitation_id] = future
            await queue.put(ServerSentEvent(
                event="mcp_elicitation",
                data=TypeAdapter(Any).dump_json(event_data),
            ))

            try:
                return await asyncio.wait_for(future, timeout=5 * 60)
            except asyncio.TimeoutError:
                return mcp.types.ElicitResult(action="cancel")
            finally:
                request.app.state.elicitations.pop(elicitation_id, None)

        async def agent_stream():
            try:
                async for event in agent.run_stream(
                    user_prompt=body.user_prompt,
                    message_history=body.message_history,
                    elicitation_callback=handle_elicitation,
                ):
                    if isinstance(event, AgentRunResultEvent):
                        # Return the final result in the same format as non streaming
                        data = AgentRunResponse(
                            new_messages=event.result.new_messages(),
                            usage=event.result.usage(),
                        ).model_dump_json()
                    else:
                        data = TypeAdapter(Any).dump_json(event).decode()
                    await queue.put(ServerSentEvent(event=event.event_kind, data=data))
            finally:
                await queue.put(None) # End stream sentinel value

        async def event_generator() -> AsyncGenerator[ServerSentEvent, None]:
            driver_task = asyncio.create_task(agent_stream())
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield event
            finally:
                # driver_task will already be complete unless the response got cancelled, then we
                # need to clean it up explicitly here.
                driver_task.cancel()
                try:
                    await driver_task
                except asyncio.CancelledError:
                    pass

        return EventSourceResponse(event_generator())
    else:
        result = await agent.run(
            user_prompt=body.user_prompt,
            message_history=body.message_history,
        )
        return AgentRunResponse(
            new_messages=result.new_messages(),
            usage=result.usage(),
        )
