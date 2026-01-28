"""
MCP server definition exposing a minimal tool and resource interface.
"""

from fastmcp import FastMCP

from .tools.echo import echo as echo_impl


mcp = FastMCP(name="MCP Server")


@mcp.tool
def echo(message: str) -> dict:
    """Return the provided message."""
    return echo_impl(message)


@mcp.resource("hello://{name}")
def hello(name: str) -> str:
    """Return a greeting for the specified name."""
    return f"Hello, {name}!"


if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8000)