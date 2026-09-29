"""
Generic planner runtime — the manifest-driven delegation engine.

`CampaignPlanner` is the domain-agnostic orchestration core the planner agent (and the
API, commit 8) drives: it turns a candidate into per-role subagent steps + dispatched
HPC jobs, and routes a finished job back to the right subagent to collect. The planner
agent's *intelligence* (gathering inputs, drafting/editing the plan with the user,
scoring via the playbook skill's script, deciding to loop or exit) sits on top and calls
these primitives — so this core is fully testable without an LLM.

`build_planner_system_prompt` inlines the playbook skill so the planner agent is
specialized by it; `build_subagents` builds one subagent per manifest role.
"""

import uuid
from pathlib import Path
from typing import Callable

from pydantic_ai.models import Model
from sqlmodel.ext.asyncio.session import AsyncSession

from ...services import campaign as campaign_service
from ..skills import read_skill
from .manifest import CampaignManifest, render_script_args
from .subagent import (
    HpcTools,
    ParsedResult,
    ResultParser,
    SubAgent,
    SubAgentOrder,
    build_skill_parser,
)


_PLANNER_BASE_PROMPT = (
    "You are the planner agent orchestrating a multi-cycle scientific simulation campaign. "
    "Follow the campaign playbook below: gather the inputs you need from the user, draft a "
    "plan and get the user's approval (their edits always take precedence), delegate "
    "simulation work to your subagents, evaluate the results against the goal metric, and "
    "loop with the user until they confirm exit. Never launch HPC work without an approved "
    "plan, and confirm before each new cycle and before exit."
)


def build_planner_system_prompt(planner_skill_dir) -> str:
    """Planner agent's system prompt: orchestration framing + the playbook skill inlined."""
    skill = read_skill(planner_skill_dir)
    return "\n\n".join(
        [_PLANNER_BASE_PROMPT, f"# Campaign playbook: {skill.name}\n\n{skill.body}"]
    )


def build_subagents(
    manifest: CampaignManifest,
    *,
    hpc: HpcTools,
    skills_dir: Path | str,
    parser_factory: Callable[[Path, str], ResultParser] | None = None,
    model: str | Model | None = None,
) -> dict[str, SubAgent]:
    """
    Build one `SubAgent` per manifest role, each specialized by its sim skill's parser.

    `model` is forwarded to `build_skill_parser`, so pass the same resolved
    `Model` the request's other agents use. Ignored when `parser_factory` is
    supplied, which is how the tests inject a parser with no LLM at all.
    """
    factory = parser_factory or (
        lambda skill_dir, role: build_skill_parser(skill_dir, role, model)
    )
    subagents: dict[str, SubAgent] = {}
    for spec in manifest.subagents:
        parser = factory(Path(skills_dir) / spec.skill, spec.role)
        subagents[spec.role] = SubAgent(role=spec.role, hpc=hpc, parser=parser)
    return subagents


class CampaignPlanner:
    """Manifest-driven delegation: candidate -> per-role steps + jobs; finished job -> collect."""

    def __init__(self, *, manifest: CampaignManifest, subagents: dict[str, SubAgent]):
        self.manifest = manifest
        self.subagents = subagents

    async def dispatch_candidate(
        self,
        session: AsyncSession,
        *,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        candidate: dict,
        cycle: int,
        cluster: str | None = None,
    ) -> list[str]:
        """Create + dispatch a step for every subagent role on this candidate. Returns job ids."""
        job_ids: list[str] = []
        for spec in self.manifest.subagents:
            subagent = self.subagents[spec.role]
            step = await campaign_service.add_step(
                session, run_id=run_id, cycle=cycle, kind=spec.role, candidate=candidate
            )
            order = SubAgentOrder(
                job=spec.job,
                candidate=candidate,
                cluster=cluster,
                # How the candidate becomes job arguments is declared per role in the
                # manifest; roles that declare nothing keep the old JSON encoding.
                script_args=render_script_args(self.manifest, spec, candidate),
            )
            result = await subagent.dispatch(
                session, step=step, user_id=user_id, order=order
            )
            job_ids.extend(result.job_ids)
        return job_ids

    def collect_files_for_role(self, role: str) -> list[str]:
        """The output files this role's parser needs, as declared in the manifest."""
        spec = self.manifest.subagent(role)
        return list(spec.collect_files) if spec else []

    async def collect_job(
        self, session: AsyncSession, *, job, files: list[str] | None = None
    ) -> ParsedResult:
        """
        Route a finished job back to its role's subagent to parse + complete the step.

        `files` defaults to the role's manifest-declared `collect_files`, so the monitor
        does not have to know what a domain's outputs are called. An explicit `files`
        argument still wins (callers and tests can override).
        """
        step = await campaign_service.get_step(session, job.step_id)
        if step is None:
            raise ValueError(f"Step {job.step_id} for job {job.job_id} not found")
        subagent = self.subagents.get(step.kind)
        if subagent is None:
            raise ValueError(f"No subagent registered for role {step.kind!r}")
        if files is None:
            files = self.collect_files_for_role(step.kind) or None
        return await subagent.collect(session, job=job, files=files)
