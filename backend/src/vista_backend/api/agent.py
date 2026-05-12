import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pydantic_ai import RunUsage, UsageLimits
from pydantic_ai.messages import ModelMessage

from ..agents.agents import build_project_agent, run_project_agent
from ..db.db import SessionDep
from ..db.schemas import ProjectPublic, ProjectTable

router = APIRouter()

class AgentRunRequest(BaseModel):
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
) -> AgentRunResponse:
    """
    Stateless chat completion that runs the full agent loop for one turn.

    Pass the message_history. The response contains the messages produced in the agent
    "turn". Append the response to your message_history to continue in the next call.
    """
    project = await session.get(ProjectTable, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    project = ProjectPublic.model_validate(project)

    agent = build_project_agent(project)
    result = await run_project_agent(project, agent,
        user_prompt = body.user_prompt,
        message_history=body.message_history,
    )
    return AgentRunResponse(
        new_messages=result.new_messages(),
        usage=result.usage(),
    )
