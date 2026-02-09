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
from .tools.execute_skill_script import execute_skill_script as execute_skill_script_impl
from .tools.image_viewer import image_viewer as image_viewer_impl

logging.basicConfig(
    filename=Path(tempfile.gettempdir()) / "vista.log",
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
)

mcp = FastMCP(name="MCP Server")


@mcp.tool()
def execute_skill_script(command: str) -> ToolResult:
    """
    Execute a script provided by a skill.

    Prefer using this tool to safely execute scripts under a skills directory.
    """
    return execute_skill_script_impl(command)

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
