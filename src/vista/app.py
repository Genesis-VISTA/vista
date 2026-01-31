"""
MCP server definition exposing tools and resources.
"""

from __future__ import annotations

from fastmcp import FastMCP

from .tools.salt_analysis import run_salt_analysis as run_salt_analysis_impl


mcp = FastMCP(name="MCP Server")


@mcp.tool
def run_salt_analysis(salt: str, data_path: str | None = None) -> dict:
    """Run the salt-analysis skill for the given salt name."""
    return run_salt_analysis_impl(salt=salt, data_path=data_path)


@mcp.resource("hello://{name}")
def hello(name: str) -> str:
    """Return a greeting for the specified name."""
    return f"Hello, {name}!"


def main() -> None:
    mcp.run(transport="http", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()