"""
MCP server definition exposing tools and resources.
"""

from __future__ import annotations
import argparse
from typing import Annotated as A
from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from fastmcp.server.apps import AppConfig, ResourceCSP
from pathlib import Path
import tempfile
import logging
import functools
from .tools.display_file import display_file as display_file_impl
from .config import settings

logging.basicConfig(
    filename=Path(tempfile.gettempdir()) / "vista.log",
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
)

mcp = FastMCP(name="MCP Server")

@mcp.tool(
    app=AppConfig(resource_uri="ui://display-file.html"),
)
def display_file(uri: A[str, "Absolute path to file, or a URI"]) -> ToolResult:
    """
    Displays a file to the user. Supports images, text, markdown, PDF, and HTML.
    """
    return display_file_impl(uri, allowed_uris=settings.allowed_uris, uri_map=settings.uri_map)

@mcp.resource("ui://display-file.html")
@functools.cache
def display_file_resource():
    return (settings.mcp_apps_dir / "display-file.html").read_text()

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "http"],
        help="Server transport mode. Default is stdio.",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host interface for HTTP transport.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for HTTP transport.",
    )
    return parser


def main(argv: list[str] | None = None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.transport == "http":
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
