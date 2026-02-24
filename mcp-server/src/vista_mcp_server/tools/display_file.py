import re
import json
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

def display_file(uri: str, allowed_uris: list[str], uri_map: dict[str, str]) -> ToolResult:
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

    return ToolResult(content=[
        TextContent(type="text", text=json.dumps({"uri": mapped_uri})),
    ])
