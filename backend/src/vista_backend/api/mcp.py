import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
import mcp.types
from pydantic import BaseModel

from ..config import settings
from ..db.db import SessionDep
from ..services import project as project_service
from ..services.chat_run import ELSEWHERE, resolve_prompt, run_for_prompt
from ..services.current_user import CurrentUser
from ..services.project_agent import (
    get_project_agent_key,
    project_agent_pool,
)


router = APIRouter(tags=["mcp"])


@router.get("/projects/{project_name}/mcp/tools")
async def mcp_tools(
    project_name: str,
    session: SessionDep,
    user: CurrentUser,
    chat_session_id: uuid.UUID | None = None,
) -> list[mcp.types.Tool]:
    project = await project_service.get_project_by_name(session, project_name, user)
    agent_key = await get_project_agent_key(
        session, project_id=project.id, chat_session_id=chat_session_id, user=user
    )
    async with project_agent_pool.get(agent_key) as agent:
        return await agent.list_tools()


class McpCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


@router.post("/projects/{project_name}/mcp/call")
async def mcp_call(
    project_name: str,
    req: McpCallRequest,
    session: SessionDep,
    user: CurrentUser,
) -> mcp.types.CallToolResult:
    if settings.deployment == "hosted":
        # Calls any tool with the server's own credentials and no confirmation.
        raise HTTPException(status_code=404, detail="Not found")
    project = await project_service.get_project_by_name(session, project_name, user)
    async with project_agent_pool.get((None, project.id)) as agent:
        try:
            return await agent.call_tool(req.name, req.arguments)
        except Exception as exc:
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=f"MCP error: {exc}")],
                isError=True,
            )


class ElicitationSubmit(BaseModel):
    id: str
    action: Literal["accept", "decline", "cancel"]
    content: dict[str, Any] | None = None


@router.post("/projects/{project_name}/elicitation")
async def mcp_elicitation(
    project_name: str, submit: ElicitationSubmit, session: SessionDep, user: CurrentUser
):
    """
    Resolve a pending MCP elicitation request (such as emitted by /projects/{id}/agent/run)
    """
    await project_service.get_project_by_name(session, project_name, user)
    # A Slack conversation's prompts are answered in Slack, by its requester.
    run = run_for_prompt(submit.id)
    if run is not None and run.channel != "app":
        raise HTTPException(status_code=409, detail=ELSEWHERE)
    if not await resolve_prompt(submit.id, submit.action, submit.content):
        raise HTTPException(
            status_code=404, detail="Elicitation not found or already resolved"
        )
    return {"ok": True}
