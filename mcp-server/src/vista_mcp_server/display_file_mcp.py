"""
MCP tool to display files. For images, reads the file and returns an inline
base64 <img> tag so that any JSON-RPC caller receives renderable HTML directly.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path
from typing import Annotated as A

from fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations
from .config import settings

logger = logging.getLogger(__name__)

mcp = FastMCP(name="Display File")


def _resolve_path(uri: str) -> Path | None:
    """
    Resolve a sandbox-style URI or absolute path to a real host path.

    Handles:
      /mnt/data/output/...  →  <project>/data/output/...
      /mnt/data/uploads/... →  <project>/data/uploads/...
      /mnt/skills/...       →  <project>/skills/...
      file:///mnt/...       →  strips the file:// prefix then resolves as above
    """
    raw = uri.strip()
    if raw.startswith("file://"):
        raw = raw[len("file://"):]

    # Map sandbox mount paths to host paths via configured volumes
    for host_path, sandbox_path, _mode in settings.volumes:
        prefix = str(sandbox_path)
        if raw.startswith(prefix):
            resolved = Path(str(host_path)) / raw[len(prefix):].lstrip("/")
            return resolved

    # If it's already an absolute host path, use as-is
    if raw.startswith("/"):
        return Path(raw)

    return None


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
    resolved = _resolve_path(uri)

    if resolved is None:
        msg = f"Could not resolve path: {uri}"
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
    await ctx.info("display_file: returning %d-char HTML for %s", len(html), resolved.name)
    return html
