"""
MCP tool to display files. Resolves a sandbox file path to a download URL served
by the Vista backend and returns that URL (plus mime type) so the UI can fetch and
render the file directly -- no binary data is sent back through the model context.
"""
import re
import logging
import mimetypes
from pathlib import PurePosixPath
from typing import Annotated as A
from urllib.parse import quote, unquote, urlsplit

from fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations
from .lib.user_config import get_vista_meta

logger = logging.getLogger(__name__)

mcp = FastMCP(name="Display File")


def resolve_uri(uri: str, uri_map: dict[str, str]) -> str:
    """
    Resolve a sandbox URI to a download URL.
    uri_map is a mapping of uri templates, where {path} represents an arbitrary path.
    E.g. 
    ```
    {
        "file:///mnt/data/output/{path}": "https://localhost:8080/files/outputs/{path}",
        "file:///mnt/data/uploads/{path}": "https://localhost:8080/files/outputs/{path}?get-param=1",
    }
    
    """
    # Sandbox paths are POSIX whatever the host is, so they're never built or parsed with
    # `Path`, which is a WindowsPath on Windows.
    if uri.startswith("/"):
        uri = "file://" + quote(uri)

    if uri.startswith("file:"):
        parts = urlsplit(uri)
        path = PurePosixPath(unquote(parts.path))
        if (
            parts.netloc not in ("", "localhost")
            or not path.is_absolute()
            or ".." in path.parts
            or "." in parts.path.split("/")
        ):
            raise ValueError(f"URI is not absolute {uri}")

    for src_template, repl_template in uri_map.items():
        prefix, sep, suffix = src_template.partition("{path}")
        if sep:
            pattern = re.escape(prefix) + "(.+)" + re.escape(suffix)
        else:
            pattern = re.escape(src_template)

        match = re.fullmatch(pattern, uri)
        if match:
            rest = match[1] if len(match.groups()) > 0 else ''
            rest = quote(unquote(rest), safe="/")
            return repl_template.replace("{path}", rest)

    raise ValueError(f"No download URL is configured for {uri}")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
async def display_file(
    ctx: Context,
    uri: A[str, "Absolute path to a file, or a file:// URI"],
) -> dict[str, str]:
    """
    Display a file (e.g. an image or plot) to the user.
    """
    vista = get_vista_meta(ctx)
    try:
        resolved = resolve_uri(uri, vista.uri_map)
    except ValueError as e:
        msg = f"Could not resolve {uri}: {e}"
        await ctx.warning(msg)
        return {"error": msg}

    mime_type = mimetypes.guess_type(uri)[0] or "application/octet-stream"
    await ctx.info(f"display_file {uri} -> {resolved}")
    return {"uri": resolved, "mime_type": mime_type, "filename": PurePosixPath(urlsplit(uri).path).name}
