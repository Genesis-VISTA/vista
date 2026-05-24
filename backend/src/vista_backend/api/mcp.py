from typing import Any, Literal

from fastapi import APIRouter, HTTPException
import mcp.types
from pydantic import BaseModel
from sqlmodel import select

from ..db.db import SessionDep
from ..db.schemas import ProjectTable
from ..utils.project import project_agent_pool, resolve_elicitation
from .auth import UserDep


router = APIRouter(tags=["mcp"])


@router.get("/projects/{project_name}/mcp/tools")
async def mcp_tools(project_name: str, session: SessionDep, user: UserDep) -> list[mcp.types.Tool]:
    project = (await session.exec(
        select(ProjectTable).where(ProjectTable.name == project_name)
    )).first()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    async with project_agent_pool.get((project.id, user.id)) as agent:
        return await agent.list_tools()


class McpCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


@router.post("/projects/{project_name}/mcp/call")
async def mcp_call(
    project_name: str, req: McpCallRequest, session: SessionDep, user: UserDep,
) -> mcp.types.CallToolResult:
    project = (await session.exec(
        select(ProjectTable).where(ProjectTable.name == project_name)
    )).first()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    async with project_agent_pool.get((project.id, user.id)) as agent:
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
async def mcp_elicitation(project_name: str, submit: ElicitationSubmit, session: SessionDep):
    """
    Resolve a pending MCP elicitation request (such as emitted by /projects/{id}/agent/run)
    """
    project = (await session.exec(
        select(ProjectTable).where(ProjectTable.name == project_name)
    )).first()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not resolve_elicitation(submit.id, submit.action, submit.content):
        raise HTTPException(status_code=404, detail="Elicitation not found or already resolved")
    return {"ok": True}
