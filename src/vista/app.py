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
from .tools.salt_analysis import run_salt_analysis as run_salt_analysis_impl
from .tools.image_viewer import image_viewer as image_viewer_impl

logging.basicConfig(
    filename=Path(tempfile.gettempdir()) / "vista.log",
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
)

mcp = FastMCP(name="MCP Server")


@mcp.tool()
def run_salt_analysis(salt: str, data_path: str | None = None) -> dict:
    """Run the salt-analysis skill for the given salt name."""
    return run_salt_analysis_impl(salt=salt, data_path=data_path)

@mcp.tool(
    meta={"ui": {"resourceUri": "ui://image-viewer"}},
)
def image_viewer(path: str) -> ToolResult:
    """
    Displays an image file to the user.
    """
    return image_viewer_impl(path)


MCP_APPS_DIR = Path(__file__).parent / 'mcp-apps'

@mcp.resource("ui://image-viewer",
    mime_type="text/html;profile=mcp-app",
)
def image_viewer_resource() -> str:
    """HTML resource for the image viewer MCP App."""
    return (MCP_APPS_DIR / "image-viewer.html").read_text()

@mcp.resource("ui://{widget}",
    mime_type="text/html;profile=mcp-app",
)
def mcp_apps(widget: str) -> str:
    """ MCP Apps """
    html_file = (MCP_APPS_DIR / f"{widget}.html").resolve()
    # Disallow any ../ etc
    if not html_file.is_relative_to(MCP_APPS_DIR) or not html_file.exists():
        raise ValueError(f"{widget} not found")
    return html_file.read_text()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", default="stdio", choices=['stdio', 'http'])
    args = parser.parse_args()
    if args.transport == "http":
        mcp.run(transport="http", host="0.0.0.0", port=8000)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
