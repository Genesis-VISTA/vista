from typing import Any, Literal

from fastapi import APIRouter, HTTPException
import mcp.types
from pydantic import BaseModel

from ..agents.agents import get_vista_mcp_server, get_dev_mcp_server
from ..utils.project import resolve_elicitation


router = APIRouter()


@router.get("/mcp/tools")
async def mcp_tools() -> list[mcp.types.Tool]:
    async with get_vista_mcp_server() as vista_mcp_server, get_dev_mcp_server() as dev_mcp_server:
        return (await vista_mcp_server.list_tools()) + (await dev_mcp_server.list_tools())


class McpCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


@router.post("/mcp/call")
async def mcp_call(req: McpCallRequest) -> mcp.types.CallToolResult:
    # TODO Should cache tool list
    async with get_vista_mcp_server() as vista_mcp_server, get_dev_mcp_server() as dev_mcp_server:
        for server in [vista_mcp_server, dev_mcp_server]:
            tools = await server.list_tools()
            if any(t.name == req.name for t in tools):
                try:
                    return await server._client.call_tool(req.name, req.arguments)
                except Exception as exc:
                    return mcp.types.CallToolResult(
                        content=[mcp.types.TextContent(type="text", text=f"MCP error: {exc}")],
                        isError=True,
                    )
    return mcp.types.CallToolResult(
        content=[mcp.types.TextContent(type="text", text=f"MCP error: no tool {req.name} found")],
        isError=True,
    )

class ElicitationSubmit(BaseModel):
    id: str
    action: Literal["accept", "decline", "cancel"]
    content: dict[str, Any] | None = None


@router.post("/mcp/elicitation")
async def mcp_elicitation(submit: ElicitationSubmit):
    """
    Resolve a pending MCP elicitation request (such as emitted by /projects/{id}/agent/run)
    """
    if not resolve_elicitation(submit.id, submit.action, submit.content):
        raise HTTPException(status_code=404, detail="Elicitation not found or already resolved")
    return {"ok": True}
