"""
Wiring between the campaign monitor and the live MCP/planner boundary.

These are the seams the monitor's injected `poll`/`collect` are built from:

  - `parse_job_state`     — pull the job STATE out of get_hpc_job_status's text output.
  - `build_status_poll`   — a monitor `poll(job)` over an MCP `invoke` closure.
  - `build_collector`     — a monitor `collect(session, job, raw)` that reconstructs the
                            campaign's planner (via an injected provider) and collects.

They're kept tiny and dependency-injected so they're unit-testable here; the planner
runtime supplies the real `invoke` (MCP + per-user credentials) and the per-job planner
provider when it starts the monitor (PR2).
"""
import re
from typing import Awaitable, Callable

from sqlmodel.ext.asyncio.session import AsyncSession

from ...db.schemas import HpcJobTable
from .planner import CampaignPlanner


_STATE_RE = re.compile(r"\bSTATE[=:]\s*(\S+)")


def parse_job_state(status_text: str) -> str:
    """Extract the job state token from get_hpc_job_status's output; 'UNKNOWN' if absent."""
    match = _STATE_RE.search(status_text or "")
    return match.group(1).strip() if match else "UNKNOWN"


InvokeTool = Callable[[str, dict], Awaitable[str]]
PlannerProvider = Callable[[AsyncSession, HpcJobTable], Awaitable[CampaignPlanner]]


def build_status_poll(invoke: InvokeTool):
    """Build a monitor `poll(job) -> (state, raw_status)` over an MCP `invoke` closure."""
    async def poll(job: HpcJobTable) -> tuple[str, str]:
        text = await invoke("get_hpc_job_status", {"job_id": job.job_id, "cluster": job.cluster})
        return parse_job_state(text), text

    return poll


def build_collector(planner_provider: PlannerProvider):
    """Build a monitor `collect(session, job, raw_status)` that delegates to the run's planner."""
    async def collect(session: AsyncSession, job: HpcJobTable, raw_status: str) -> None:
        planner = await planner_provider(session, job)
        await planner.collect_job(session, job=job)

    return collect
