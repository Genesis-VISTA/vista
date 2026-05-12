import uuid
from typing import AsyncGenerator, Any
import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, TypeAdapter
from pydantic_ai import RunUsage
from pydantic_ai.messages import ModelMessage
from mcp.client.session import ClientSession
from mcp.shared.context import RequestContext
import mcp.types
from sse_starlette.sse import EventSourceResponse
from sse_starlette.event import ServerSentEvent
from ..agents.agents import build_project_agent, run_project_agent, run_project_agent_stream
from ..db.db import SessionDep
from ..db.schemas import ProjectPublic, ProjectTable

router = APIRouter()

class AgentRunRequest(BaseModel):
    stream: bool = False
    """ If True, stream the response as Server-Sent Events """
    user_prompt: str
    """ The user prompt to the agent """
    message_history: list[ModelMessage] = []
    """ message_history (as returned from a previous call)"""

class AgentRunResponse(BaseModel):
    new_messages: list[ModelMessage]
    """
    All the new messages from the agent turn.

    Append the result to message_history for the next call.
    """
    usage: RunUsage

@router.post("/projects/{project_id}/agent/run", response_model=AgentRunResponse)
async def agent_run(
    project_id: uuid.UUID, body: AgentRunRequest, session: SessionDep, request: Request,
) -> AgentRunResponse | Response:
    """
    Stateless chat completion that runs the full agent loop for one turn.

    Pass the message_history. The response contains the messages produced in the agent
    "turn". Append the response to your message_history to continue in the next call.

    When stream=True, returns Server-Sent Events:
      - event: text-delta  data: "<incremental text chunk>"
      - event: done        data: {"new_messages": [...], "usage": {...}}

    Supports MCP elicitation in streaming mode, elicitation requests look like:
    - event: mcp-elicitation  data: {"elicitationId": "<uuid>", "mode": "form", "message": "...", ...}
    The client must POST the response to /mcp/elicitation.
    """
    project = await session.get(ProjectTable, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    project = ProjectPublic.model_validate(project)

    if body.stream:
        # We have to do some custom async queue to handle interleaving the MCP Elicitation events
        # and the primary agent stream events.
        queue: asyncio.Queue[ServerSentEvent | None] = asyncio.Queue()

        async def handle_elicitation(
            context: RequestContext[ClientSession, Any, Any],
            params: mcp.types.ElicitRequestParams,
        ) -> mcp.types.ElicitResult:
            elicitation_id = str(uuid.uuid4())

            if not isinstance(params, mcp.types.ElicitRequestFormParams):
                return mcp.types.ElicitResult(action="cancel")

            event_data = {
                "elicitationId": elicitation_id,
                "mode": params.mode,
                "message": params.message,
                "requestedSchema": params.requestedSchema,
            }

            future: asyncio.Future[mcp.types.ElicitResult] = asyncio.get_running_loop().create_future()
            request.app.state.elicitations[elicitation_id] = future
            await queue.put(ServerSentEvent(
                event="mcp-elicitation",
                data=TypeAdapter(Any).dump_json(event_data),
            ))

            try:
                return await asyncio.wait_for(future, timeout=5 * 60)
            except asyncio.TimeoutError:
                return mcp.types.ElicitResult(action="cancel")
            finally:
                request.app.state.elicitations.pop(elicitation_id, None)

        agent = build_project_agent(project, elicitation_callback=handle_elicitation)

        async def agent_stream():
            try:
                async with run_project_agent_stream(
                    project, agent,
                    user_prompt=body.user_prompt,
                    message_history=body.message_history,
                ) as stream:
                    async for delta in stream.stream_text(delta=True):
                        await queue.put(ServerSentEvent(event="text-delta", data=delta))

                    await queue.put(ServerSentEvent(
                        event="done",
                        data=TypeAdapter(Any).dump_json({
                            "new_messages": stream.new_messages(),
                            "usage": stream.usage(),
                        }),
                    ))
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
        agent = build_project_agent(project)
        result = await run_project_agent(project, agent,
            user_prompt=body.user_prompt,
            message_history=body.message_history,
        )
        return AgentRunResponse(
            new_messages=result.new_messages(),
            usage=result.usage(),
        )
