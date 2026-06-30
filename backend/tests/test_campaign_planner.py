"""Tests for the planner runtime: McpHpcTools, subagent factory, and CampaignPlanner delegation.

No live LLM or MCP: the HPC boundary and the result parsers are injected.
"""
import json
import shlex

import pytest

from vista_backend.agents.campaign.hpc_tools import McpHpcTools, parse_submit_summary
from vista_backend.agents.campaign.manifest import CampaignManifest
from vista_backend.agents.campaign.planner import (
    CampaignPlanner,
    build_planner_system_prompt,
    build_subagents,
    encode_candidate_args,
)
from vista_backend.agents.campaign.subagent import (
    CallableResultParser,
    ParsedResult,
    SubmittedJobInfo,
)
from vista_backend.db.schemas import ProjectTable
from vista_backend.services import campaign as campaign_service


PLANNER_SKILL_MD = """---
name: test-planner
description: A test campaign playbook.
---

# Test planner playbook

Phase A: gather inputs. Phase B: draft a plan. Phase G: exit on user confirmation.
"""

MANIFEST_YAML = """
domain: testdomain
metrics:
  primary: {name: SCORE, target: 1.1}
subagents:
  - {role: alpha, skill: alpha-skill, job: alpha_job}
  - {role: beta, skill: beta-skill, job: beta_job}
"""


# --- encode_candidate_args (the candidate -> job script_args contract) -----

def _mcp_shlex_roundtrip(script_args: str) -> list[str]:
    """Reproduce what a candidate's script_args survives end to end: the vista MCP submit
    tool's `shlex.join(shlex.split(...))`, then the job shell splitting `"$@"` again."""
    mcp_cmd = shlex.join(shlex.split(script_args))  # submit_job_mcp.py
    return shlex.split(mcp_cmd)                      # the job script's argv


def test_encode_candidate_args_survives_shlex_roundtrip_as_one_json_token():
    candidate = {"embed_dim": 768, "lr": 0.0005, "tensor_parallel": 2, "context_parallel": 1}
    argv = _mcp_shlex_roundtrip(encode_candidate_args(candidate))
    # The whole candidate arrives as a single argv entry the wrapper can json.loads back.
    assert len(argv) == 1
    assert json.loads(argv[0]) == candidate


def test_encode_candidate_args_none_for_empty_candidate():
    assert encode_candidate_args(None) is None
    assert encode_candidate_args({}) is None


class FakeHpcTools:
    """Increments job ids so a candidate's two role-jobs get distinct PKs."""
    def __init__(self):
        self.n = 0
        self.submitted_jobs: list[str] = []

    async def submit(self, *, job, cluster, node_count, duration, script_args):
        self.n += 1
        self.submitted_jobs.append(job)
        return SubmittedJobInfo(job_id=f"job-{self.n}", cluster=cluster or "frontier")

    async def status(self, *, job_id, cluster):
        return f"STATE=COMPLETED for {job_id}"

    async def fetch_outputs(self, *, job_id, files, cluster):
        return f"files={files}"


def _manifest() -> CampaignManifest:
    import yaml

    return CampaignManifest.model_validate(yaml.safe_load(MANIFEST_YAML))


def _planner(hpc) -> CampaignPlanner:
    manifest = _manifest()
    subagents = build_subagents(
        manifest,
        hpc=hpc,
        skills_dir="/unused",
        parser_factory=lambda skill_dir, role: CallableResultParser(
            lambda **_: ParsedResult(ok=True, metrics={"SCORE": 1.2})
        ),
    )
    return CampaignPlanner(manifest=manifest, subagents=subagents)


async def _make_run(session, alice):
    project = ProjectTable(name="planner-project")
    session.add(project)
    await session.flush()
    return await campaign_service.create_campaign(
        session, project_id=project.id, user_id=alice.id,
        domain="testdomain", planner_skill="test-planner",
    )


# --- McpHpcTools -----------------------------------------------------------

def test_parse_submit_summary():
    text = "job_id: 12345\ncluster: frontier\nnodes: 2\nduration: 1:00:00"
    assert parse_submit_summary(text) == ("12345", "frontier")


def test_parse_submit_summary_raises_without_job_id():
    with pytest.raises(ValueError):
        parse_submit_summary("cluster: frontier\nnodes: 2")


@pytest.mark.anyio
async def test_mcp_hpc_tools_submit_parses_and_status_passes_through():
    calls = []

    async def invoke(tool, args):
        calls.append((tool, args))
        if tool == "submit_hpc_job":
            return "job_id: 777\ncluster: odo\nnodes: 1"
        return f"raw output for {tool}"

    hpc = McpHpcTools(invoke)
    info = await hpc.submit(job="neutronics", cluster="odo", node_count=1, duration=None, script_args="--x 1")
    assert info.job_id == "777"
    assert info.cluster == "odo"
    assert calls[0][1]["job"] == "neutronics"
    assert calls[0][1]["script_args"] == "--x 1"

    status = await hpc.status(job_id="777", cluster="odo")
    assert "raw output for get_hpc_job_status" in status


# --- build_subagents -------------------------------------------------------

def test_build_subagents_one_per_role():
    hpc = FakeHpcTools()
    subagents = build_subagents(
        _manifest(),
        hpc=hpc,
        skills_dir="/unused",
        parser_factory=lambda skill_dir, role: CallableResultParser(lambda **_: ParsedResult(ok=True)),
    )
    assert set(subagents) == {"alpha", "beta"}
    assert subagents["alpha"].role == "alpha"


# --- CampaignPlanner -------------------------------------------------------

@pytest.mark.anyio
async def test_dispatch_candidate_creates_a_step_and_job_per_role(session, alice):
    run = await _make_run(session, alice)
    hpc = FakeHpcTools()
    planner = _planner(hpc)

    job_ids = await planner.dispatch_candidate(
        session, run_id=run.id, user_id=alice.id,
        candidate={"x": 0.5}, cycle=0, cluster="frontier",
    )

    assert sorted(job_ids) == ["job-1", "job-2"]
    assert hpc.submitted_jobs == ["alpha_job", "beta_job"]

    steps = await campaign_service.list_steps(session, run_id=run.id, cycle=0)
    assert {s.kind for s in steps} == {"alpha", "beta"}
    assert all(s.status == "dispatched" for s in steps)
    # Each step's candidate carried through.
    assert all(s.candidate == {"x": 0.5} for s in steps)


@pytest.mark.anyio
async def test_collect_job_routes_to_role_subagent_and_completes_step(session, alice):
    run = await _make_run(session, alice)
    hpc = FakeHpcTools()
    planner = _planner(hpc)
    await planner.dispatch_candidate(
        session, run_id=run.id, user_id=alice.id, candidate={"x": 0.5}, cycle=0,
    )

    job = await campaign_service.get_job(session, "job-1")
    parsed = await planner.collect_job(session, job=job, files=["result.json"])

    assert parsed.ok is True
    assert parsed.metrics["SCORE"] == 1.2
    step = await campaign_service.get_step(session, job.step_id)
    assert step.status == "completed"


@pytest.mark.anyio
async def test_collect_job_raises_for_unknown_role(session, alice):
    run = await _make_run(session, alice)
    planner = _planner(FakeHpcTools())
    # A step whose kind has no registered subagent.
    orphan = await campaign_service.add_step(session, run_id=run.id, cycle=0, kind="gamma")
    job = await campaign_service.record_job(
        session, job_id="orphan-1", step_id=orphan.id, user_id=alice.id, cluster="odo"
    )
    with pytest.raises(ValueError):
        await planner.collect_job(session, job=job)


# --- planner system prompt -------------------------------------------------

def test_build_planner_system_prompt_inlines_playbook(tmp_path):
    skill_dir = tmp_path / "test-planner"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(PLANNER_SKILL_MD)

    prompt = build_planner_system_prompt(skill_dir)
    assert "planner agent orchestrating" in prompt
    assert "Test planner playbook" in prompt  # the playbook body is inlined
    assert "exit on user confirmation" in prompt
