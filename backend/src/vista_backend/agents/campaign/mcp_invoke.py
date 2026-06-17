"""
Build the live MCP `invoke` closure used by the planner's `McpHpcTools` and the monitor.

`build_invoke` is the testable core: it wraps a `call_tool(name, args, metadata)` callable and
injects the per-user VISTA metadata the HPC tools need. In production that callable is an
MCPServer's `direct_call_tool`, which threads `metadata` as the request `_meta` (the same
`{"vista": …}` channel `_make_mcp_process_tool_call` uses during an agent run) and returns the
tool's already-unwrapped text result.

`build_mcp_invoke` is the thin live wiring over the vista MCP server; `project_paths_for`
reproduces the ProjectAgent sandbox-volume layout the HPC tools resolve log/output paths against.
"""
import json
import uuid
from typing import Any, Awaitable, Callable

from ...config import settings
from ...db.schemas import UserPublicWithConfig
from .hpc_tools import InvokeTool


# A metadata-aware tool caller, matching MCPServer.direct_call_tool(name, args, metadata).
CallToolFn = Callable[[str, dict, dict | None], Awaitable[Any]]


def project_paths_for(project_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, str]:
    """The sandbox-volume paths the HPC tools resolve log/output dirs against (per ProjectAgent)."""
    volume_root = settings.data_dir / "volumes" / f"{project_id}-{user_id}"
    return {
        "skills_dir": str(volume_root / "skills"),
        "output_dir": str(volume_root / "data" / "output"),
        "uploads_dir": str(volume_root / "data" / "uploads"),
    }


def build_metadata(user: UserPublicWithConfig, project_paths: dict[str, str]) -> dict[str, Any]:
    """The `{"vista": {"user", "project_paths"}}` metadata the vista MCP HPC tools read."""
    return {
        "vista": {
            "user": user.model_dump(mode="json"),
            "project_paths": project_paths,
        }
    }


def build_invoke(
    call_tool: CallToolFn,
    *,
    user: UserPublicWithConfig,
    project_paths: dict[str, str],
) -> InvokeTool:
    """Wrap a metadata-aware `call_tool` into an `invoke(name, args) -> text` closure."""
    metadata = build_metadata(user, project_paths)

    async def invoke(name: str, args: dict) -> str:
        result = await call_tool(name, args, metadata)
        return result if isinstance(result, str) else json.dumps(result)

    return invoke


def build_mcp_invoke(user: UserPublicWithConfig, project_paths: dict[str, str]) -> InvokeTool:
    """Live `invoke` over the vista MCP server (direct_call_tool threads metadata + unwraps text)."""
    # Local import: agents.agents pulls in the full agent stack; keep it off module-load.
    from ..agents import get_vista_mcp_server

    server = get_vista_mcp_server()
    return build_invoke(server.direct_call_tool, user=user, project_paths=project_paths)
