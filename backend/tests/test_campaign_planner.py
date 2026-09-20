"""Tests for the planner runtime: McpHpcTools, subagent factory, and CampaignPlanner delegation.

No live LLM or MCP: the HPC boundary and the result parsers are injected.
"""

import pytest

from vista_backend.agents.campaign.hpc_tools import McpHpcTools, parse_submit_summary
from vista_backend.agents.inference import build_inference_model
from vista_backend.agents.campaign.manifest import (
    CampaignManifest,
    render_script_args,
)
from vista_backend.agents.campaign.planner import (
    CampaignPlanner,
    build_planner_system_prompt,
    build_subagents,
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
        session,
        project_id=project.id,
        user_id=alice.id,
        domain="testdomain",
        planner_skill="test-planner",
    )


# --- McpHpcTools -----------------------------------------------------------


def test_parse_submit_summary():
    text = "job_id: 12345\ncluster: frontier\nnodes: 2\nduration: 1:00:00"
    assert parse_submit_summary(text) == {
        "job_id": "12345",
        "cluster": "frontier",
        "log_path": "",
        "err_path": "",
        "output_dir": "",
    }


def test_parse_submit_summary_keeps_the_rendered_paths():
    """
    They are the only record of where a job's files are outside the MCP server.

    Dropping them left every job row with a blank `log_path` and `output_dir`, so
    the report attached to a debate's FINDING post named no file a reader could
    open — and nothing pointed at the stderr file where a failed run explains
    itself.
    """
    text = (
        "job_id: 44039\ncluster: odo\nnodes: 1\nduration: 1:00:00\n"
        "log_path: /gpfs/out/44039/log-44039.out\n"
        "err_path: /gpfs/out/44039/log-44039.err\n"
        "output_dir: /gpfs/out/44039"
    )
    parsed = parse_submit_summary(text)
    assert parsed["log_path"] == "/gpfs/out/44039/log-44039.out"
    assert parsed["err_path"] == "/gpfs/out/44039/log-44039.err"
    assert parsed["output_dir"] == "/gpfs/out/44039"


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
    info = await hpc.submit(
        job="neutronics",
        cluster="odo",
        node_count=1,
        duration=None,
        script_args="--x 1",
    )
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
        parser_factory=lambda skill_dir, role: CallableResultParser(
            lambda **_: ParsedResult(ok=True)
        ),
    )
    assert set(subagents) == {"alpha", "beta"}
    assert subagents["alpha"].role == "alpha"


def test_build_subagents_threads_the_resolved_model_to_every_parser(tmp_path):
    """
    A resolved `Model` reaches each role's real parser agent.

    Built without a `parser_factory` so the production `build_skill_parser`
    path runs: it used to resolve its own model from `Settings`, which cannot
    see a key from the user's settings row, so a campaign's parsers reached a
    different endpoint than the agent that dispatched the job.
    """
    for role in ("alpha", "beta"):
        skill_dir = tmp_path / f"{role}-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {role}-skill\ndescription: d\n---\n\nParse {role}.\n",
            encoding="utf-8",
        )

    model = build_inference_model(
        "openai-chat:test-model", api_key="row-key", base_url="https://user.example/v1"
    )
    subagents = build_subagents(
        _manifest(), hpc=FakeHpcTools(), skills_dir=tmp_path, model=model
    )

    for role in ("alpha", "beta"):
        assert subagents[role].parser.agent.model is model, f"{role} built its own"


# --- CampaignPlanner -------------------------------------------------------


@pytest.mark.anyio
async def test_dispatch_candidate_creates_a_step_and_job_per_role(session, alice):
    run = await _make_run(session, alice)
    hpc = FakeHpcTools()
    planner = _planner(hpc)

    job_ids = await planner.dispatch_candidate(
        session,
        run_id=run.id,
        user_id=alice.id,
        candidate={"x": 0.5},
        cycle=0,
        cluster="frontier",
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
        session,
        run_id=run.id,
        user_id=alice.id,
        candidate={"x": 0.5},
        cycle=0,
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
    orphan = await campaign_service.add_step(
        session, run_id=run.id, cycle=0, kind="gamma"
    )
    job = await campaign_service.record_job(
        session, job_id="orphan-1", step_id=orphan.id, user_id=alice.id, cluster="odo"
    )
    with pytest.raises(ValueError):
        await planner.collect_job(session, job=job)


# --- planner system prompt -------------------------------------------------


def test_build_planner_system_prompt_inlines_playbook(tmp_path):
    skill_dir = tmp_path / "test-planner"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(PLANNER_SKILL_MD, encoding="utf-8")

    prompt = build_planner_system_prompt(skill_dir)
    assert "planner agent orchestrating" in prompt
    assert "Test planner playbook" in prompt  # the playbook body is inlined
    assert "exit on user confirmation" in prompt


# --- render_script_args (pure) ---------------------------------------------

RENDER_MANIFEST_YAML = """
domain: testdomain
variables:
  - {name: a, range: [0, 1]}
  - {name: b, range: [0, 1]}
  - {name: c, range: [0, 1]}
metrics:
  primary: {name: SCORE}
subagents:
  - role: alpha
    skill: alpha-skill
    job: alpha_job
    args:
      encoding: flags
      map: {a: --ay, b: --bee}
      extra: "--fixed 3"
  - role: beta
    skill: beta-skill
    job: beta_job
    args:
      encoding: flags
      map: {c: --see}
  - {role: gamma, skill: gamma-skill, job: gamma_job}
  - role: delta
    skill: delta-skill
    job: delta_job
    args:
      encoding: json
"""


def _render_manifest() -> CampaignManifest:
    import yaml

    return CampaignManifest.model_validate(yaml.safe_load(RENDER_MANIFEST_YAML))


def test_render_flags_uses_manifest_variable_order_not_candidate_order():
    m = _render_manifest()
    # Candidate deliberately in reverse declaration order.
    rendered = render_script_args(m, m.subagent("alpha"), {"b": 2, "a": 1})
    assert rendered == "--ay 1 --bee 2 --fixed 3"


def test_render_flags_excludes_unmapped_variables():
    m = _render_manifest()
    # `c` is declared and present, but alpha does not map it.
    rendered = render_script_args(m, m.subagent("alpha"), {"a": 1, "b": 2, "c": 3})
    assert "--see" not in rendered
    assert rendered == "--ay 1 --bee 2 --fixed 3"


def test_render_gives_each_role_its_own_subset():
    m = _render_manifest()
    candidate = {"a": 1, "b": 2, "c": 3}
    assert render_script_args(m, m.subagent("alpha"), candidate) == "--ay 1 --bee 2 --fixed 3"
    assert render_script_args(m, m.subagent("beta"), candidate) == "--see 3"


def test_render_without_extra_omits_it():
    m = _render_manifest()
    assert render_script_args(m, m.subagent("beta"), {"c": 0.5}) == "--see 0.5"


def test_render_json_encoding_is_opt_in():
    m = _render_manifest()
    assert render_script_args(m, m.subagent("delta"), {"a": 1}) == '{"a": 1}'


def test_render_without_args_block_is_byte_identical_to_pre_change_behavior():
    """The pre-contract encoding was json.dumps(candidate); non-adopters must match it."""
    import json

    m = _render_manifest()
    candidate = {"a": 1, "b": 2.5, "c": "x"}
    assert render_script_args(m, m.subagent("gamma"), candidate) == json.dumps(candidate)


def test_render_without_args_block_and_empty_candidate_is_none():
    m = _render_manifest()
    assert render_script_args(m, m.subagent("gamma"), {}) is None
    assert render_script_args(m, m.subagent("gamma"), None) is None


def test_render_flags_with_bool_uses_store_true_style():
    m = _render_manifest()
    spec = m.subagent("beta")
    assert render_script_args(m, spec, {"c": True}) == "--see"
    assert render_script_args(m, spec, {"c": False}) is None


@pytest.mark.anyio
async def test_dispatch_candidate_submits_rendered_flags(session, alice):
    """The rendered string is what actually reaches submit_hpc_job."""
    import yaml

    manifest = CampaignManifest.model_validate(yaml.safe_load(RENDER_MANIFEST_YAML))
    hpc = FakeHpcTools()
    hpc.script_args_seen = []

    original_submit = hpc.submit

    async def recording_submit(*, job, cluster, node_count, duration, script_args):
        hpc.script_args_seen.append((job, script_args))
        return await original_submit(
            job=job, cluster=cluster, node_count=node_count,
            duration=duration, script_args=script_args,
        )

    hpc.submit = recording_submit
    subagents = build_subagents(
        manifest,
        hpc=hpc,
        skills_dir="/unused",
        parser_factory=lambda skill_dir, role: CallableResultParser(
            lambda **_: ParsedResult(ok=True)
        ),
    )
    planner = CampaignPlanner(manifest=manifest, subagents=subagents)
    run = await _make_run(session, alice)

    await planner.dispatch_candidate(
        session, run_id=run.id, user_id=alice.id,
        candidate={"a": 1, "b": 2, "c": 3}, cycle=0,
    )

    seen = dict(hpc.script_args_seen)
    assert seen["alpha_job"] == "--ay 1 --bee 2 --fixed 3"
    assert seen["beta_job"] == "--see 3"
    assert seen["gamma_job"] == '{"a": 1, "b": 2, "c": 3}'  # unchanged for non-adopters
