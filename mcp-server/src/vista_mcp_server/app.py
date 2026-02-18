"""
MCP server definition exposing tools and resources.
"""

from __future__ import annotations
import argparse
from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from pathlib import Path
import tempfile
import logging
import functools
from .tools.image_viewer import image_viewer as image_viewer_impl

logging.basicConfig(
    filename=Path(tempfile.gettempdir()) / "vista.log",
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
)

mcp = FastMCP(name="MCP Server")

@mcp.tool(
    meta={"ui": {"resourceUri": "ui://image-viewer"}},
)
def image_viewer(path: str) -> ToolResult:
    """
    Displays an image file to the user.
    """
    return image_viewer_impl(path)


MCP_APPS_DIR = Path(__file__).parent / 'mcp-apps'

# We could use a single "Resource Template" to dynamically fetch these, but Goose scans the resource
# list to look for ui resources, so a template confuses it.
for widget in MCP_APPS_DIR.glob("*.html"):
    html = widget.read_text()
    mcp.resource(f"ui://{widget.stem}",
        name=widget.stem,
        mime_type="text/html;profile=mcp-app",
    )(functools.partial(lambda html: html, html))

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
