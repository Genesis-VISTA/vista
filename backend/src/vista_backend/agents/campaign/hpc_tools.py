"""
MCP-backed `HpcTools` — implements the subagent's HPC boundary over the VISTA MCP
HPC tools (submit_hpc_job / get_hpc_job_status / get_hpc_job_outputs).

The actual MCP call (which threads the user's HPC credentials via MCP metadata and
unwraps the CallToolResult) is supplied as an injected `invoke(tool_name, args) -> str`
closure, built by the planner runtime from the ProjectAgent's MCP session (commit 8).
Keeping that closure injected means this layer is unit-testable with a fake and carries
the one piece of real logic that belongs here: parsing submit_hpc_job's summary.
"""

from typing import Awaitable, Callable

from .subagent import SubmittedJobInfo


InvokeTool = Callable[[str, dict], Awaitable[str]]


def parse_submit_summary(text: str) -> tuple[str, str]:
    """Pull (job_id, cluster) out of submit_hpc_job's multi-line ground-truth summary."""
    job_id = ""
    cluster = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("job_id:"):
            job_id = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("cluster:"):
            cluster = stripped.split(":", 1)[1].strip()
    if not job_id:
        raise ValueError(f"Could not parse job_id from submit_hpc_job output: {text!r}")
    return job_id, cluster


class McpHpcTools:
    """`HpcTools` that runs the VISTA MCP HPC tools through an injected `invoke` closure."""

    def __init__(self, invoke: InvokeTool):
        self._invoke = invoke

    async def submit(
        self,
        *,
        job: str,
        cluster: str | None,
        node_count: int | None,
        duration: str | None,
        script_args: str | None,
    ) -> SubmittedJobInfo:
        args: dict = {"job": job}
        if cluster is not None:
            args["cluster"] = cluster
        if node_count is not None:
            args["node_count"] = node_count
        if duration is not None:
            args["duration"] = duration
        if script_args is not None:
            args["script_args"] = script_args
        text = await self._invoke("submit_hpc_job", args)
        job_id, parsed_cluster = parse_submit_summary(text)
        # The backend records job_id + cluster; the MCP server's persistent registry
        # (resolves rendered log/output paths) is what makes the later status/outputs
        # calls restart-safe, so we don't need the paths on this side.
        return SubmittedJobInfo(
            job_id=job_id, cluster=parsed_cluster or (cluster or "")
        )

    async def status(self, *, job_id: str, cluster: str) -> str:
        return await self._invoke(
            "get_hpc_job_status", {"job_id": job_id, "cluster": cluster}
        )

    async def fetch_outputs(
        self, *, job_id: str, files: list[str], cluster: str
    ) -> str:
        return await self._invoke(
            "get_hpc_job_outputs",
            {"job_id": job_id, "files": files, "cluster": cluster},
        )
