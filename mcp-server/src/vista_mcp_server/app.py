"""
MCP server definition exposing tools and resources.
"""

from __future__ import annotations
import argparse
from fastmcp import FastMCP
from pathlib import Path
import tempfile
import logging
from .config import settings
from .display_file_mcp import mcp as display_file_mcp
from .sandbox_mcp import mcp as sandbox_mcp
from .web_search_mcp import mcp as web_search_mcp

logging.basicConfig(
    filename=Path(tempfile.gettempdir()) / "vista.log",
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
)

mcp = FastMCP(name="VISTA MCP Server")
mcp.mount(display_file_mcp)
mcp.mount(sandbox_mcp)
mcp.mount(web_search_mcp)

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
