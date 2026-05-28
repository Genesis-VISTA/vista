"""
MCP tool to display files. For images, reads the file and returns an inline
base64 <img> tag so that any JSON-RPC caller receives renderable HTML directly.
"""
import re
import base64
import logging
import mimetypes
from pathlib import Path
from typing import Annotated as A

from fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations
from .config import settings
from .lib.user_config import ProjectPaths, get_vista_meta

logger = logging.getLogger(__name__)

mcp = FastMCP(name="Display File")


def _build_uri_map(paths: ProjectPaths) -> dict[str, str]:
    """
    Build the per-call sandbox->host URI map from the calling agent's volume layout.
    Mirrors the bind mounts set up in `ProjectAgent.__aenter__` /
    `backend/.../agents.py::get_dev_mcp_server`.
    """
    sandbox_to_host = {
        "/mnt/skills/": paths.skills_dir,
        "/mnt/data/output/": paths.output_dir,
        "/mnt/data/uploads/": paths.uploads_dir,
    }
    uri_map: dict[str, str] = {}
    for sandbox_prefix, host_dir in sandbox_to_host.items():
        if not host_dir:
            continue
        host_uri = Path(host_dir).resolve().as_uri()
        uri_map[f"file://{sandbox_prefix}"] = host_uri + "/"
    return uri_map


def resolve_uri(uri: str, allowed_uris: list[str], uri_map: dict[str, str]) -> str:
    """
    Resolve a uri from within the sandbox to an absolute path on the real host.
    """
    if uri.startswith("/"):
        uri = Path(uri).as_uri()

    if uri.startswith("file://"):
        path = Path.from_uri(uri)
        if not path.is_absolute() or ".." in path.parts or "." in path.parts:
            raise ValueError(f"URI is not absolute {uri}")

    if not any(re.fullmatch(pattern, uri) for pattern in allowed_uris):
        raise ValueError(f"URI not allowed: {uri}")

    for prefix, replacement in uri_map.items():
        if uri.startswith(prefix):
            uri = replacement + uri[len(prefix):]
            break
    
    return uri


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
async def display_file(
    ctx: Context,
    uri: A[str, "Absolute path to an image file, or a file:// URI"],
) -> str:
    """
    Display an image file to the user. Reads the file, base64-encodes it,
    and returns an HTML <img> tag that the UI renders directly.
    """
    try:
        # Currently only file uris are allowed. We may adjust that if we switch back to using MCP Apps for more advanced rendering
        uri_map = _build_uri_map(get_vista_meta(ctx).project_paths)
        resolved = Path.from_uri(resolve_uri(uri, settings.allowed_uris, uri_map))
    except ValueError as e:
        msg = f"Could not resolve {uri}: {e}"
        await ctx.warning(msg)
        return msg

    if not resolved.exists():
        msg = f"File not found: {resolved}"
        await ctx.warning(msg)
        return msg

    ext = resolved.suffix.lower()
    if ext not in _IMAGE_EXTENSIONS:
        # For non-image files, return the text content
        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
            return text[:50_000]  # cap at 50k chars
        except Exception as exc:
            return f"Cannot read file {resolved}: {exc}"

    # Read image and return as base64 HTML
    mime_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
    try:
        data = resolved.read_bytes()
    except Exception as exc:
        msg = f"Cannot read image {resolved}: {exc}"
        await ctx.error(msg)
        return msg

    b64 = base64.b64encode(data).decode("ascii")
    html = (
        f'<img src="data:{mime_type};base64,{b64}" '
        f'alt="{resolved.name}" '
        f'style="max-width:100%;height:auto;display:block;margin:0 auto;" />'
    )
    await ctx.info(f"display_file: returning {len(html)}-char HTML for {resolved.name}")
    return html
