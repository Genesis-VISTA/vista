import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
import mcp.types
from pydantic import BaseModel

from ..db.db import SessionDep
from ..services import project as project_service
from ..services.project_agent import get_project_agent_key, project_agent_pool, resolve_elicitation
from ..services.auth import UserDep


router = APIRouter(tags=["mcp"])


@router.get("/projects/{project_name}/mcp/tools")
async def mcp_tools(
    project_name: str,
    session: SessionDep,
    user: UserDep,
    chat_session_id: uuid.UUID | None = None,
) -> list[mcp.types.Tool]:
    project = await project_service.get_project_by_name(session, project_name, user)
    agent_key = await get_project_agent_key(
        session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat_session_id,
    )
    async with project_agent_pool.get(agent_key) as agent:
        return await agent.list_tools()


class McpCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = {}
    chat_session_id: uuid.UUID | None = None


@router.post("/projects/{project_name}/mcp/call")
async def mcp_call(
    project_name: str, req: McpCallRequest, session: SessionDep, user: UserDep,
) -> mcp.types.CallToolResult:
    project = await project_service.get_project_by_name(session, project_name, user)
    agent_key = await get_project_agent_key(
        session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=req.chat_session_id,
    )
    async with project_agent_pool.get(agent_key) as agent:
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
async def mcp_elicitation(project_name: str, submit: ElicitationSubmit, session: SessionDep, user: UserDep):
    """
    Resolve a pending MCP elicitation request (such as emitted by /projects/{id}/agent/run)
    """
    project = await project_service.get_project_by_name(session, project_name, user)
    if not resolve_elicitation(submit.id, submit.action, submit.content):
        raise HTTPException(status_code=404, detail="Elicitation not found or already resolved")
    return {"ok": True}
