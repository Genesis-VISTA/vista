from fastmcp.tools.tool import ToolResult
from mcp.types import ImageContent, Annotations, TextContent
import mimetypes
import base64
from pathlib import Path

def image_viewer(path: str) -> ToolResult:
    """
    Displays an image file to the user using an MCP App UI.

    Args:
        path: Path to the image file to display.
    """
    if not Path(path).exists():
        raise ValueError(f"Image file not found: {path}")

    mime_type, encoding = mimetypes.guess_type(path)
    if not mime_type or not mime_type.startswith('image/'):
        raise ValueError(f"{path} is not an image")

    image_data = base64.b64encode(Path(path).read_bytes()).decode("utf-8")

    return ToolResult(
        content=[
            ImageContent(
                type="image",
                data=image_data,
                mimeType=mime_type,
                annotations=Annotations(
                    audience=["user"],
                    priority=0.9
                )
            ),
            TextContent(type = "text", text = f"Showed user {path}"),
        ],
    )
