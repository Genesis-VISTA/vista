"""
MCP server definition exposing a minimal tool and resource interface.
"""

from fastmcp import FastMCP
import argparse
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", default="stdio", choices=['stdio', 'http'])
    args = parser.parse_args()
    if args.transport == "http":
        mcp.run(transport="http", host="0.0.0.0", port=8000)
    else:
        mcp.run()
