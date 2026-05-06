from typing import Any
from fastapi import APIRouter
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
