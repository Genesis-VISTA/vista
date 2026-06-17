"""
Wiring between the campaign monitor and the live MCP/planner boundary.

These are the seams the monitor's `poll`/`collect` are built from, plus the per-job
reconstruction the live monitor needs (each job carries its own owning user + campaign,
so credentials and the planner are derived per job from the DB):

  - `parse_job_state`        — pull the job STATE out of get_hpc_job_status's text output.
  - `build_invoke_for_job`   — the per-user MCP `invoke` for a job's owning user.
  - `build_planner_for_job`  — reconstruct the job's campaign planner from its planner skill.
  - `build_status_poll`      — a monitor `poll(session, job)` that derives the invoke per job.
  - `build_collector`        — a monitor `collect(session, job, raw)` that delegates to the
                               run's planner (defaults to `build_planner_for_job`).

The MCP `invoke` builder and the sim-skill parser factory are injected, so everything here
is unit-testable with fakes; production uses `build_mcp_invoke` + the LLM skill parser.
"""
import re
from pathlib import Path
from typing import Awaitable, Callable

from sqlmodel.ext.asyncio.session import AsyncSession

from ...db.schemas import HpcJobTable, UserPublicWithConfig, UserTable
from ...services import campaign as campaign_service
from .hpc_tools import InvokeTool, McpHpcTools
from .manifest import load_manifest
from .mcp_invoke import build_mcp_invoke, project_paths_for
from .planner import CampaignPlanner, build_subagents
from .subagent import ResultParser


_STATE_RE = re.compile(r"\bSTATE[=:]\s*(\S+)")


def parse_job_state(status_text: str) -> str:
    """Extract the job state token from get_hpc_job_status's output; 'UNKNOWN' if absent."""
    match = _STATE_RE.search(status_text or "")
    return match.group(1).strip() if match else "UNKNOWN"


# (user, project_paths) -> invoke; defaults to the live build_mcp_invoke.
InvokeBuilder = Callable[[UserPublicWithConfig, dict], InvokeTool]
ParserFactory = Callable[[Path, str], ResultParser]
PlannerProvider = Callable[[AsyncSession, HpcJobTable], Awaitable[CampaignPlanner]]


async def _job_run_user_paths(session: AsyncSession, job: HpcJobTable):
    """Load (run, user, sandbox-volume paths) for a job — the per-job context for live wiring."""
    step = await campaign_service.get_step(session, job.step_id)
    if step is None:
        raise ValueError(f"Step {job.step_id} for job {job.job_id} not found")
    run = await campaign_service.require_campaign(session, step.run_id)
    user_row = await session.get(UserTable, job.user_id)
    if user_row is None:
        raise ValueError(f"User {job.user_id} for job {job.job_id} not found")
    user = UserPublicWithConfig.model_validate(user_row)
    paths = project_paths_for(run.project_id, run.user_id)
    return run, user, paths


async def build_invoke_for_job(
    session: AsyncSession, job: HpcJobTable, *, invoke_builder: InvokeBuilder = build_mcp_invoke
) -> InvokeTool:
    """The MCP `invoke` for a job, bound to its owning user's credentials + sandbox paths."""
    _run, user, paths = await _job_run_user_paths(session, job)
    return invoke_builder(user, paths)


async def build_planner_for_job(
    session: AsyncSession,
    job: HpcJobTable,
    *,
    invoke_builder: InvokeBuilder = build_mcp_invoke,
    parser_factory: ParserFactory | None = None,
    model: str | None = None,
) -> CampaignPlanner:
    """Reconstruct the planner for a job's campaign from its planner skill + manifest."""
    run, user, paths = await _job_run_user_paths(session, job)
    skills_dir = paths["skills_dir"]
    manifest = load_manifest(Path(skills_dir) / run.planner_skill)
    hpc = McpHpcTools(invoke_builder(user, paths))
    subagents = build_subagents(
        manifest, hpc=hpc, skills_dir=skills_dir, parser_factory=parser_factory, model=model
    )
    return CampaignPlanner(manifest=manifest, subagents=subagents)


def build_status_poll(*, invoke_builder: InvokeBuilder = build_mcp_invoke):
    """Build a monitor `poll(session, job) -> (state, raw_status)`, deriving the invoke per job."""
    async def poll(session: AsyncSession, job: HpcJobTable) -> tuple[str, str]:
        invoke = await build_invoke_for_job(session, job, invoke_builder=invoke_builder)
        text = await invoke("get_hpc_job_status", {"job_id": job.job_id, "cluster": job.cluster})
        return parse_job_state(text), text

    return poll


def build_collector(planner_provider: PlannerProvider = build_planner_for_job):
    """Build a monitor `collect(session, job, raw_status)` that delegates to the run's planner."""
    async def collect(session: AsyncSession, job: HpcJobTable, raw_status: str) -> None:
        planner = await planner_provider(session, job)
        await planner.collect_job(session, job=job)

    return collect
