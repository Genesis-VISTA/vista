"""
An in-process stand-in for the MCP toolsets `ProjectAgent` normally mounts.

Unit and component tests must not launch the STDIO `dev_mcp_server` or reach
the HTTP `vista_mcp_server`, so this module exposes the same tool *names* and
argument shapes over a plain `FunctionToolset`. Return values mirror the
dry-run summaries from
[`dry_run.py`](../../../mcp_servers/vista_mcp_server/src/vista_mcp_server/dry_run.py)
so assertions written against the fake stay meaningful.

To exercise the real servers instead, drop the `toolsets=` override in
[`agent.py`](./agent.py) and enter the agent (`async with agent:`) so PydanticAI
starts the MCP connections. Mark such tests `integration` — they need
`vista_mcp_server` reachable at `settings.mcp_url` and `uv` on PATH.
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.toolsets.function import FunctionToolset


@dataclass
class ToolCallRecord:
    name: str
    args: dict[str, Any]


@dataclass
class FakeMcp:
    """A fake toolset plus the call log tests assert against."""

    toolset: FunctionToolset
    calls: list[ToolCallRecord] = field(default_factory=list)

    def names_called(self) -> list[str]:
        return [c.name for c in self.calls]

    def args_for(self, name: str) -> dict[str, Any]:
        """Args of the first call to `name`. Raises if it was never called."""
        for call in self.calls:
            if call.name == name:
                return call.args
        raise AssertionError(f"{name} was not called; saw {self.names_called()}")


def fake_mcp() -> FakeMcp:
    """
    Build the fake MCP toolset.

    Covers the tools Milestone B's agent turns exercise: `rag_search`,
    `submit_hpc_job`, `display_file`, and `run_bash`.
    """
    toolset = FunctionToolset()
    calls: list[ToolCallRecord] = []

    def record(name: str, **args) -> None:
        calls.append(ToolCallRecord(name=name, args=args))

    @toolset.tool_plain
    def rag_search(query: str, kb_slug: str | None = None, n_results: int = 5) -> str:
        """Search an indexed literature corpus within a Knowledge Base."""
        record("rag_search", query=query, kb_slug=kb_slug, n_results=n_results)
        return f"[1] passage about {query!r} from kb={kb_slug}"

    @toolset.tool_plain
    def submit_hpc_job(
        job: str,
        cluster: str | None = None,
        node_count: int | None = None,
        duration: str | None = None,
        script_args: str | None = None,
    ) -> str:
        """Submit a curated HPC job to a cluster."""
        record(
            "submit_hpc_job",
            job=job,
            cluster=cluster,
            node_count=node_count,
            duration=duration,
            script_args=script_args,
        )
        return (
            f"JOB_ID=dry-{len(calls):012d}\n"
            f"CLUSTER={cluster or 'odo'}\n"
            "STATE=PENDING\n\n"
            "(dry-run: waiting in synthetic queue; logs appear once it runs)"
        )

    @toolset.tool_plain
    def get_hpc_job_status(job_id: str, cluster: str | None = None) -> str:
        """Get the status and logs of a submitted HPC job."""
        record("get_hpc_job_status", job_id=job_id, cluster=cluster)
        return (
            f"JOB_ID={job_id}\n"
            f"CLUSTER={cluster or 'odo'}\n"
            "STATE=COMPLETED\n"
            "EXIT_CODE=0\n\n"
            "--- LOGS ---\n(dry-run synthetic log; no real cluster output)\n"
            "--- OUTPUT FILES ---\n(none)"
        )

    @toolset.tool_plain
    def display_file(uri: str) -> dict[str, str]:
        """Display a file (e.g. an image or plot) to the user."""
        record("display_file", uri=uri)
        return {
            "uri": uri,
            "mime_type": "image/png",
            "filename": uri.rsplit("/", 1)[-1],
        }

    @toolset.tool_plain
    def run_bash(command: str) -> str:
        """Run a bash command inside the sandbox."""
        record("run_bash", command=command)
        return f"(fake sandbox) ran: {command}"

    return FakeMcp(toolset=toolset, calls=calls)
