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
from .display_file import mcp as display_file_mcp
from .shell import mcp as shell_mcp

logging.basicConfig(
    filename=Path(tempfile.gettempdir()) / "vista.log",
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
)

mcp = FastMCP(name="VISTA MCP Server")
mcp.mount(display_file_mcp)
mcp.mount(shell_mcp)


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
