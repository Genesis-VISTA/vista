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

import logging
import re
from pathlib import Path
from typing import Awaitable, Callable

from sqlmodel.ext.asyncio.session import AsyncSession

from pydantic_ai.models import Model

from ...db.schemas import (
    HpcJobTable,
    ProjectTable,
    UserPublicWithConfig,
    UserTable,
)
from ...services import campaign as campaign_service
from ..forum import simulation
from ..forum.simulation import DEBATE_DOMAIN
from ..forum.project_forum import build_client_for
from ...services.campaign_monitor import CampaignMonitor, normalize_state
from .hpc_tools import InvokeTool, McpHpcTools
from .manifest import load_manifest
from .mcp_invoke import build_mcp_invoke, project_paths_for
from ..inference import build_model_for
from .planner import CampaignPlanner, build_subagents
from .subagent import ResultParser


logger = logging.getLogger(__name__)


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
    if run.session_id is None and run.domain != DEBATE_DOMAIN:
        # A chat-backed campaign that lost its conversation cannot be rebuilt, so
        # refusing here stops the monitor polling something nothing can act on.
        #
        # A debate-commissioned job never had a conversation — its result goes to
        # a forum thread — and `CampaignMonitor._is_orphaned` already says so in
        # as many words. The exemption was written there and not here, so every
        # debate job failed on its first poll with a complaint about a sandbox
        # volume that the line below does not use a session to resolve.
        raise ValueError(
            f"Campaign {run.id} has no session_id; cannot resolve its sandbox volume."
        )
    user_row = await session.get(UserTable, job.user_id)
    if user_row is None:
        raise ValueError(f"User {job.user_id} for job {job.job_id} not found")
    user = UserPublicWithConfig.model_validate(user_row)
    paths = project_paths_for(run.project_id, run.user_id)
    return run, user, paths


async def build_invoke_for_job(
    session: AsyncSession,
    job: HpcJobTable,
    *,
    invoke_builder: InvokeBuilder = build_mcp_invoke,
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
    model: str | Model | None = None,
) -> CampaignPlanner:
    """
    Reconstruct the planner for a job's campaign from its planner skill + manifest.

    With no explicit `model`, this resolves one from the job's own user row, so
    a parser built here reaches the same endpoint and credential as the agent
    that dispatched the job. The monitor runs in the background with no request
    context and never passes a model, which is the path that made this matter:
    without it the parser fell back to `Settings` alone and could not see a key
    entered in the settings modal.
    """
    run, user, paths = await _job_run_user_paths(session, job)
    skills_dir = paths["skills_dir"]
    manifest = load_manifest(Path(skills_dir) / run.planner_skill)
    hpc = McpHpcTools(invoke_builder(user, paths))
    subagents = build_subagents(
        manifest,
        hpc=hpc,
        skills_dir=skills_dir,
        parser_factory=parser_factory,
        model=model or build_model_for(user),
    )
    return CampaignPlanner(manifest=manifest, subagents=subagents)


def build_status_poll(*, invoke_builder: InvokeBuilder = build_mcp_invoke):
    """Build a monitor `poll(session, job) -> (state, raw_status)`, deriving the invoke per job."""

    async def poll(session: AsyncSession, job: HpcJobTable) -> tuple[str, str]:
        invoke = await build_invoke_for_job(session, job, invoke_builder=invoke_builder)
        text = await invoke(
            "get_hpc_job_status", {"job_id": job.job_id, "cluster": job.cluster}
        )
        return parse_job_state(text), text

    return poll


def build_collector(planner_provider: PlannerProvider = build_planner_for_job):
    """
    Build a monitor `collect(session, job, raw_status, ok)` for an ordinary campaign.

    A finished job goes to the run's planner, which parses it. A failed one does
    not — there is nothing to parse — but its status text is kept on the step
    rather than discarded, because that text is the scheduler's log and the only
    account of why the job died. Recording the bare state left the planner, the
    UI and anyone reading later with the word "failed" and no way past it.

    No `files` argument is passed: `CampaignPlanner.collect_job` resolves the job's role
    to its manifest-declared `collect_files`, so the monitor stays domain-agnostic and a
    role that declares none is collected from status text alone, as before.
    """

    async def collect(
        session: AsyncSession, job: HpcJobTable, raw_status: str, ok: bool
    ) -> None:
        if not ok:
            await campaign_service.update_step(
                session,
                step_id=job.step_id,
                status="failed",
                result={"state": normalize_state(job.state), "outputs": raw_status},
            )
            return
        planner = await planner_provider(session, job)
        await planner.collect_job(session, job=job)

    return collect


def build_debate_aware_collector(
    planner_provider: PlannerProvider = build_planner_for_job,
):
    """
    A collector that routes a finished job to whoever commissioned it.

    A debate-commissioned job has no planner and no chat session — its result
    goes back to a forum thread, posted under the identity that asked for it.
    Everything else is an ordinary campaign step and goes to its planner as before.
    """
    campaign_collect = build_collector(planner_provider)

    async def collect(
        session: AsyncSession, job: HpcJobTable, raw_status: str, ok: bool
    ) -> None:
        step = await campaign_service.get_step(session, job.step_id)
        run = (
            await campaign_service.get_campaign(session, step.run_id) if step else None
        )
        if run is not None and run.domain == DEBATE_DOMAIN:
            # The forum belongs to the campaign's project, which is the same
            # project the debate that commissioned this job belongs to. There is
            # no deployment-wide forum to post into any more.
            project = await session.get(ProjectTable, run.project_id)
            if project is None:
                logger.warning(
                    "job %s: its campaign has no project, so its result has no "
                    "forum to go to",
                    job.job_id,
                )
                return
            client = build_client_for(project)
            # Failures are posted too. `post_result` has always written them
            # correctly — "did not complete", with the log attached — but the
            # monitor only ever called it on success, so a debate that lost a run
            # left no trace of it on the thread at all. A reader saw a hypothesis
            # argued without the test, and nothing to say the test had been tried.
            await simulation.post_result(
                session,
                client,
                job,
                state=parse_job_state(raw_status),
                ok=ok,
                outputs=raw_status,
            )
            return
        await campaign_collect(session, job, raw_status, ok)

    return collect


def build_default_monitor() -> CampaignMonitor:
    """The production monitor: poll job status + collect results via the live MCP/planner wiring."""
    return CampaignMonitor(
        poll=build_status_poll(), collect=build_debate_aware_collector()
    )
