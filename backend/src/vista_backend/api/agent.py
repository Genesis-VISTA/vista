import uuid
from typing import AsyncGenerator, Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, TypeAdapter
from pydantic_ai import RunUsage
from pydantic_ai.messages import ModelMessage
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

@router.post("/projects/{project_id}/agent/run")
async def agent_run(
    project_id: uuid.UUID, body: AgentRunRequest, session: SessionDep,
) -> AgentRunResponse | Response:
    """
    Stateless chat completion that runs the full agent loop for one turn.

    Pass the message_history. The response contains the messages produced in the agent
    "turn". Append the response to your message_history to continue in the next call.

    When stream=True, returns Server-Sent Events:
      - event: text-delta  data: "<incremental text chunk>"
      - event: done        data: {"new_messages": [...], "usage": {...}}
    """
    project = await session.get(ProjectTable, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    project = ProjectPublic.model_validate(project)

    agent = build_project_agent(project)

    if body.stream:
        async def event_generator() -> AsyncGenerator[ServerSentEvent, None]:
            async with run_project_agent_stream(
                project, agent,
                user_prompt=body.user_prompt,
                message_history=body.message_history,
            ) as stream:
                async for delta in stream.stream_text(delta=True):
                    yield ServerSentEvent(event="text-delta", data=delta)

                yield ServerSentEvent(
                    event="done",
                    data=TypeAdapter(Any).dump_json({
                        "new_messages": stream.new_messages(),
                        "usage": stream.usage(),
                    }),
                )
        return EventSourceResponse(event_generator())
    else:
        result = await run_project_agent(project, agent,
            user_prompt=body.user_prompt,
            message_history=body.message_history,
        )
        return AgentRunResponse(
            new_messages=result.new_messages(),
            usage=result.usage(),
        )
