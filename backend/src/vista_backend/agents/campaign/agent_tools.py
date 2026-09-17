"""
Conversational campaign driver — the campaign tools the planner LLM calls.

`register_campaign_tools(agent, deps)` attaches the campaign lifecycle tools to any PydanticAI
agent (the production ProjectAgent in campaign mode, or a test agent). The tools call the
campaign service + `CampaignPlanner` via the injected `deps`, so the driver is decoupled from
ProjectAgent's MCP/PALISADE plumbing and unit-testable with a `FunctionModel`. The planner
skill (the campaign playbook) tells the LLM when and how to call these.

HITL is hybrid: structured intake (salt/ranges/platform/targets/budget) is captured into the
spec via `set_campaign_spec`; plan approval, edits, and continue/exit decisions happen
conversationally. `dispatch_cycle` launches HPC work and returns immediately — the monitor
emails the user and fills in results as jobs finish.
"""

import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelRetry
from sqlmodel.ext.asyncio.session import AsyncSession

from ...db.schemas import CampaignRunTable
from ...services import campaign as campaign_service
from .planner import CampaignPlanner


PlannerForRun = Callable[[AsyncSession, CampaignRunTable], Awaitable[CampaignPlanner]]


def _noop(_msg: str) -> None:
    pass


@dataclass
class CampaignDriverDeps:
    """What the campaign tools need, injected so the driver isn't coupled to ProjectAgent."""

    project_id: uuid.UUID
    user_id: uuid.UUID
    session_id: uuid.UUID | None
    """ The chat session driving the campaign; recorded on the run so the monitor can find its sandbox. """
    get_session: Callable[[], AsyncSession | None]
    get_planner: PlannerForRun
    emit_progress: Callable[[str], None] = _noop


def register_campaign_tools(agent: Agent, deps: CampaignDriverDeps) -> None:
    """Attach the campaign lifecycle tools (`start_campaign` … `finish_campaign`) to `agent`."""

    def _session() -> AsyncSession:
        session = deps.get_session()
        if session is None:
            raise RuntimeError("No active database session for campaign tools.")
        return session

    async def _require_run(session: AsyncSession, run_id_str: str) -> CampaignRunTable:
        try:
            run_id = uuid.UUID(run_id_str)
        except ValueError:
            raise ModelRetry(f"Invalid run_id {run_id_str!r}.")
        try:
            return await campaign_service.require_campaign_in_project(
                session, run_id=run_id, project_id=deps.project_id
            )
        except ValueError:
            raise ModelRetry(f"Campaign {run_id_str} not found in this project.")

    @agent.tool_plain
    async def start_campaign(
        planner_skill: str, domain: str, title: str | None = None
    ) -> str:
        """Begin a campaign. `planner_skill` and `domain` come from the active playbook skill."""
        if deps.session_id is None:
            # A campaign must be tied to a chat session: that session's id keys the sandbox
            # volume the monitor reconstructs the planner from, and is what lets the campaign
            # resume after the long HPC wait. Stateless runs can't be durably resumed.
            raise ModelRetry(
                "A campaign needs an active conversation so it can be resumed and monitored "
                "after its HPC jobs finish. Ask the user to start (or select) a conversation, "
                "then start the campaign again."
            )
        run = await campaign_service.create_campaign(
            _session(),
            project_id=deps.project_id,
            user_id=deps.user_id,
            session_id=deps.session_id,
            domain=domain,
            planner_skill=planner_skill,
            title=title,
        )
        return (
            f"Started campaign. run_id={run.id} status={run.status}. "
            "Gather the inputs from the user, then call set_campaign_spec."
        )

    @agent.tool_plain
    async def set_campaign_spec(run_id: str, spec: dict) -> str:
        """Persist the agreed spec (variables, ranges, platform, targets, budget); moves to planning."""
        session = _session()
        run = await _require_run(session, run_id)
        await campaign_service.update_campaign(
            session, run_id=run.id, spec=spec, status="planning"
        )
        return f"Spec saved for {run.id} (status=planning). Draft a plan, get user approval, then save_campaign_plan."

    @agent.tool_plain
    async def save_campaign_plan(run_id: str, plan: list[dict]) -> str:
        """Persist the user-approved numbered plan (the editable source of truth)."""
        session = _session()
        run = await _require_run(session, run_id)
        await campaign_service.save_plan(session, run_id=run.id, plan=plan)
        return f"Plan saved for {run.id} ({len(plan)} step(s))."

    @agent.tool_plain
    async def dispatch_cycle(
        run_id: str, candidates: list[dict], cycle: int, cluster: str | None = None
    ) -> str:
        """Dispatch the subagents (per the manifest) for each candidate in a cycle. Returns job ids."""
        session = _session()
        run = await _require_run(session, run_id)
        planner = await deps.get_planner(session, run)
        all_jobs: list[str] = []
        for candidate in candidates:
            job_ids = await planner.dispatch_candidate(
                session,
                run_id=run.id,
                user_id=deps.user_id,
                candidate=candidate,
                cycle=cycle,
                cluster=cluster,
            )
            all_jobs.extend(job_ids)
            deps.emit_progress(
                f"Dispatched cycle {cycle} for {candidate}: jobs {', '.join(job_ids)}"
            )
        await campaign_service.set_status(session, run_id=run.id, status="running")
        return (
            f"Dispatched {len(all_jobs)} job(s) for cycle {cycle}: {', '.join(all_jobs)}. "
            "The user will be emailed as each completes; results appear on the steps."
        )

    @agent.tool_plain
    async def get_campaign_status(run_id: str) -> str:
        """Summarize the campaign: overall status, per-step state, and per-job state."""
        session = _session()
        run = await _require_run(session, run_id)
        steps = await campaign_service.list_steps(session, run_id=run.id)
        jobs = await campaign_service.list_jobs_for_run(session, run_id=run.id)
        lines = [f"campaign {run.id} status={run.status} title={run.title!r}"]
        for s in steps:
            lines.append(
                f"  step {s.kind} cycle={s.cycle} status={s.status} result={'yes' if s.result else 'no'}"
            )
        for j in jobs:
            lines.append(
                f"  job {j.job_id} {j.job_name} state={j.state} collected={j.result_collected}"
            )
        return "\n".join(lines)

    @agent.tool_plain
    async def finish_campaign(run_id: str, status: str = "exited") -> str:
        """Close the campaign ('converged' or 'exited') once the user confirms."""
        if status not in ("converged", "exited"):
            raise ModelRetry("finish_campaign status must be 'converged' or 'exited'.")
        session = _session()
        run = await _require_run(session, run_id)
        await campaign_service.set_status(session, run_id=run.id, status=status)
        return f"Campaign {run.id} marked {status}."
