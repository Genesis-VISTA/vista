from typing import Any, Literal
import asyncio

from fastapi import APIRouter, HTTPException, Request
import mcp.types
from pydantic import BaseModel

from ..agents.agents import get_mcp_server


router = APIRouter()


@router.get("/mcp/tools")
async def mcp_tools() -> list[mcp.types.Tool]:
    async with get_mcp_server() as server:
        return await server.list_tools()


class McpCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


@router.post("/mcp/call")
async def mcp_call(req: McpCallRequest) -> mcp.types.CallToolResult:
    server = get_mcp_server()
    async with server:
        try:
            return await server._client.call_tool(req.name, req.arguments)
        except Exception as exc:
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=f"MCP error: {exc}")],
                isError=True,
            )


class ElicitationSubmit(BaseModel):
    id: str
    action: Literal["accept", "decline", "cancel"]
    content: dict[str, Any] | None = None


@router.post("/mcp/elicitation")
async def mcp_elicitation(submit: ElicitationSubmit, request: Request):
    """
    Resolve a pending MCP elicitation request (such as emitted by /projects/{id}/agent/run)
    """
    future: asyncio.Future | None = request.app.state.elicitations.pop(submit.id, None)
    if future is None or future.done():
        raise HTTPException(status_code=404, detail="Elicitation not found or already resolved")
    future.set_result(mcp.types.ElicitResult(
        action=submit.action,
        content=submit.content if submit.action == "accept" else None,
    ))
    return {"ok": True}
