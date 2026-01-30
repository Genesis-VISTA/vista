"""
Deterministic client that invokes the run_salt_analysis MCP tool.
"""

from __future__ import annotations

import asyncio
from fastmcp import Client

MCP_URL = "http://127.0.0.1:8000/mcp"


async def run() -> None:
    async with Client(MCP_URL) as client:
        result = await client.call_tool(
            "run_salt_analysis",
            {"salt": "AlCl3-KCl"},
        )
        payload = result.structured_content
        print({"ok": payload["ok"], "plot_path": payload.get("plot_path")})

def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()