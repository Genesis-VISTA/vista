"""
MCP Server adding a disply_file tool using MCP Apps to display arbitrary file types.
"""

from __future__ import annotations
from typing import Annotated as A
from fastmcp import FastMCP
from fastmcp.server.apps import AppConfig, ResourceCSP
from pydantic import BaseModel
import re
import functools

from .config import settings

mcp = FastMCP(name="Display File")

def convert_uri(uri: str, allowed_uris: list[str], uri_map: dict[str, str]) -> str:
    if uri.startswith("/"):
        # Handle if the model puts a path instead of a uri
        uri = "file://" + uri

    if not any(re.search(pattern, uri) for pattern in allowed_uris):
        raise ValueError(f"URI not allowed: {uri}")

    mapped_uri = uri
    for prefix, replacement in uri_map.items():
        if uri.startswith(prefix):
            mapped_uri = replacement + uri[len(prefix):]
            break
    
    return mapped_uri

class DisplayFileResult(BaseModel):
    uri: str
    # Add comment so the model doesn't try to display the result in markdown itself
    comment: str = "The file has been displayed to the user."

@mcp.tool(
    app=AppConfig(resource_uri="ui://display-file.html"),
)
def display_files(uri: A[str, "Absolute path to file, or a URI"]) -> DisplayFileResult:
    """
    Displays a file to the user. Supports images, text, markdown, PDF, and HTML.
    """
    uri = convert_uri(uri, settings.allowed_uris, settings.uri_map)
    return DisplayFileResult(uri = uri)

@mcp.resource("ui://display-file.html")
@functools.cache
def display_file_resource():
    return (settings.mcp_apps_dir / "display-file.html").read_text()
