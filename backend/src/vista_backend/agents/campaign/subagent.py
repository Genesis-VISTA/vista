"""
Generic subagent runtime — a specialist worker specialized by a *simulation skill*.

The runtime is domain-agnostic plumbing; the domain knowledge lives in the sim skill
(see docs/multi-agent-framework.md). A subagent has two operations:

  - `dispatch(order)` — submit the HPC job for a candidate, record it against the
    campaign step, and mark the step dispatched. Returns the job id(s).
  - `collect(job)`    — read the finished job's status/outputs, parse them into a
    structured result (via the skill-specialized parser), write the result onto the
    step, and mark the job collected.

Both the HPC tools and the result parser are *injected* so the runtime is decoupled
from MCP transport (credentials, Globus, etc., wired in the planner runtime) and from
any domain's output format. Tests inject fakes; the planner runtime injects the
MCP-backed tools and a skill-built parser.
"""

import inspect
import uuid
from typing import Any, Awaitable, Callable, Protocol

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model
from ..inference import build_inference_model
from sqlmodel.ext.asyncio.session import AsyncSession

from ...config import settings
from ...db.schemas import CampaignStepTable, HpcJobTable
from ...services import campaign as campaign_service
from ..skills import read_skill


# --------------------------------------------------------------------------- #
# Contracts
# --------------------------------------------------------------------------- #


class SubAgentOrder(BaseModel):
    """An order the planner hands to a subagent for one candidate."""

    job: str
    """ The `hpc_jobs/<name>` to submit (resolved from the campaign manifest's role→job binding). """
    candidate: dict[str, Any] | None = None
    """ The candidate composition/parameters this order evaluates. """
    cluster: str | None = None
    node_count: int | None = None
    duration: str | None = None
    script_args: str | None = None
    """ Extra args passed to the job script (typically the candidate encoded for the solver). """


class SubmittedJobInfo(BaseModel):
    """What an HPC submission yields — enough to poll status and fetch outputs later."""

    job_id: str
    cluster: str
    log_path: str | None = None
    output_dir: str | None = None


class DispatchResult(BaseModel):
    step_id: uuid.UUID
    job_ids: list[str]


class ParsedResult(BaseModel):
    """
    Structured result of parsing a finished job's outputs. Domain-agnostic: the
    metric values live in `metrics` (e.g. {"TBR": 1.18, "melting_point_c": 480});
    the domain's scorer (a planner-skill script) interprets them.
    """

    ok: bool
    summary: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)


class HpcTools(Protocol):
    """The HPC operations a subagent needs. Implemented over the MCP tools by the planner runtime."""

    async def submit(
        self,
        *,
        job: str,
        cluster: str | None,
        node_count: int | None,
        duration: str | None,
        script_args: str | None,
    ) -> SubmittedJobInfo: ...

    async def status(self, *, job_id: str, cluster: str) -> str: ...

    async def fetch_outputs(
        self, *, job_id: str, files: list[str], cluster: str
    ) -> str: ...


class ResultParser(Protocol):
    """Turns a finished job's raw status/outputs into a structured `ParsedResult`."""

    async def parse(
        self, *, candidate: dict[str, Any] | None, raw_status: str, raw_outputs: str
    ) -> ParsedResult: ...


class CallableResultParser:
    """Adapt a plain function into a `ResultParser` (handy for stubs and script-backed parsers).

    Accepts either a sync function returning `ParsedResult` or an async one returning an
    awaitable of it.
    """

    def __init__(
        self,
        fn: Callable[..., ParsedResult | Awaitable[ParsedResult]],
    ):
        self._fn = fn

    async def parse(
        self, *, candidate: dict[str, Any] | None, raw_status: str, raw_outputs: str
    ) -> ParsedResult:
        result = self._fn(
            candidate=candidate, raw_status=raw_status, raw_outputs=raw_outputs
        )
        if inspect.isawaitable(result):
            result = await result
        return result


# --------------------------------------------------------------------------- #
# Skill-specialized prompting (used by the LLM-backed parser)
# --------------------------------------------------------------------------- #

_SUBAGENT_BASE_PROMPT = (
    "You are the {role} subagent in a scientific simulation campaign. You receive the raw "
    "status and output files from a finished HPC job and extract the structured result the "
    "planner needs. Follow the simulation skill below for exactly which quantities to read "
    "and how to interpret the solver's output. Report values you can find; set ok=false and "
    "explain in summary if the job clearly failed or the outputs are missing."
)


def build_subagent_system_prompt(skill_dir, role: str) -> str:
    """Build the parser agent's system prompt: role framing + the sim skill's body inlined."""
    parts = [_SUBAGENT_BASE_PROMPT.format(role=role)]
    skill = read_skill(skill_dir)
    parts.append(f"# Simulation skill: {skill.name}\n\n{skill.body}")
    return "\n\n".join(parts)


def build_parse_user_prompt(
    *, candidate: dict[str, Any] | None, raw_status: str, raw_outputs: str
) -> str:
    import json

    return "\n\n".join(
        [
            f"Candidate parameters:\n{json.dumps(candidate or {}, indent=2)}",
            f"--- JOB STATUS / LOGS ---\n{raw_status}",
            f"--- OUTPUT FILES ---\n{raw_outputs or '(none fetched)'}",
            "Extract the structured result.",
        ]
    )


class AgentResultParser:
    """A `ResultParser` backed by a PydanticAI agent specialized by the sim skill."""

    def __init__(self, agent: Agent[None, ParsedResult], system_prompt: str):
        self.agent = agent
        self.system_prompt = system_prompt

    async def parse(
        self, *, candidate: dict[str, Any] | None, raw_status: str, raw_outputs: str
    ) -> ParsedResult:
        result = await self.agent.run(
            build_parse_user_prompt(
                candidate=candidate, raw_status=raw_status, raw_outputs=raw_outputs
            )
        )
        return result.output


def build_skill_parser(
    skill_dir, role: str, model: str | Model | None = None
) -> AgentResultParser:
    """
    Construct the LLM-backed parser for a role, specialized by its sim skill.

    `model` accepts an already-resolved `Model` as well as a `provider:name`
    string, and callers should pass one: the parser's endpoint and credential
    have to match the rest of the request. Left as `None` this falls back to
    `Settings` alone, which on a single-user install cannot see the key the
    researcher entered in the settings modal -- so the parser would fail, or
    reach a different model than the agent that dispatched the job.
    """
    system_prompt = build_subagent_system_prompt(skill_dir, role)
    agent = Agent(
        model=build_inference_model(model or settings.model),
        output_type=ParsedResult,
        system_prompt=system_prompt,
    )
    return AgentResultParser(agent, system_prompt)


# --------------------------------------------------------------------------- #
# The subagent
# --------------------------------------------------------------------------- #


class SubAgent:
    """A specialist worker for one role (e.g. "neutronics"), driven by injected HPC tools + parser."""

    def __init__(self, *, role: str, hpc: HpcTools, parser: ResultParser):
        self.role = role
        self.hpc = hpc
        self.parser = parser

    async def dispatch(
        self,
        session: AsyncSession,
        *,
        step: CampaignStepTable,
        user_id: uuid.UUID,
        order: SubAgentOrder,
    ) -> DispatchResult:
        """Submit the job for `order`, record it against `step`, and mark the step dispatched."""
        info = await self.hpc.submit(
            job=order.job,
            cluster=order.cluster,
            node_count=order.node_count,
            duration=order.duration,
            script_args=order.script_args,
        )
        await campaign_service.record_job(
            session,
            job_id=info.job_id,
            step_id=step.id,
            user_id=user_id,
            cluster=info.cluster,
            job_name=order.job,
            log_path=info.log_path,
            output_dir=info.output_dir,
        )
        await campaign_service.set_step_status(
            session, step_id=step.id, status="dispatched"
        )
        return DispatchResult(step_id=step.id, job_ids=[info.job_id])

    async def collect(
        self,
        session: AsyncSession,
        *,
        job: HpcJobTable,
        files: list[str] | None = None,
    ) -> ParsedResult:
        """Read the finished job's outputs, parse them, and complete the step."""
        raw_status = await self.hpc.status(job_id=job.job_id, cluster=job.cluster)
        raw_outputs = ""
        if files:
            raw_outputs = await self.hpc.fetch_outputs(
                job_id=job.job_id, files=files, cluster=job.cluster
            )

        step = await campaign_service.get_step(session, job.step_id)
        candidate = step.candidate if step is not None else None
        parsed = await self.parser.parse(
            candidate=candidate, raw_status=raw_status, raw_outputs=raw_outputs
        )

        await campaign_service.update_step(
            session,
            step_id=job.step_id,
            status="completed" if parsed.ok else "failed",
            result=parsed.model_dump(mode="json"),
        )
        await campaign_service.update_job(
            session, job_id=job.job_id, result_collected=True
        )
        return parsed
