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


SUMMARY_FIELDS = ("job_id", "cluster", "log_path", "err_path", "output_dir")
"""The `key: value` lines `submit_hpc_job` reports back. Absent ones stay empty."""


def parse_submit_summary(text: str) -> dict[str, str]:
    """
    Read `submit_hpc_job`'s multi-line ground-truth summary.

    Returns every field it reported, not just the two we used to take. The paths
    matter: they are rendered at submission and used to be kept only in the MCP
    server's own registry, so Vista's job rows had blank `log_path` and
    `output_dir` and the report attached to a debate's FINDING post named no file
    anyone could go and read.
    """
    found = {k: "" for k in SUMMARY_FIELDS}
    for line in text.splitlines():
        key, sep, value = line.strip().partition(":")
        if sep and key in found:
            found[key] = value.strip()
    if not found["job_id"]:
        raise ValueError(f"Could not parse job_id from submit_hpc_job output: {text!r}")
    return found


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
        summary = parse_submit_summary(text)
        # The paths are recorded here as well as in the MCP server's registry, and
        # the duplication earns its keep: the registry makes *its own* later status
        # calls restart-safe, while this copy is what lets anything outside that
        # process — a job row, a report attached to a forum post, a human reading
        # the thread — say where the job's log and outputs actually are.
        return SubmittedJobInfo(
            job_id=summary["job_id"],
            cluster=summary["cluster"] or (cluster or ""),
            log_path=summary["log_path"] or None,
            output_dir=summary["output_dir"] or None,
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
