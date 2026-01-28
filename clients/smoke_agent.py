"""
Minimal deterministic agent for testing MCP tool connectivity.
"""

from __future__ import annotations

import asyncio
from fastmcp import Client


MCP_URL = "http://127.0.0.1:8000/mcp"

async def run_agent() -> None:
    """Run a fixed sequence of MCP tool calls."""
    client = Client(MCP_URL)

    async with client:
        result = await client.call_tool(
            "echo",
            {"message": "Validate MCP agent-tool wiring"},
        )

        print("Agent result:")
        print(result.structured_content)



def main() -> None:
    """Entry point."""
    asyncio.run(run_agent())


if __name__ == "__main__":
    main()