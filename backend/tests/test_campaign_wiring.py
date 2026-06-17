"""Tests for the monitor<->MCP/planner wiring seams (parse, per-job invoke/planner, poll, collect)."""
from pathlib import Path

import pytest

from vista_backend.agents.campaign.manifest import CampaignManifest
from vista_backend.agents.campaign.mcp_invoke import project_paths_for
from vista_backend.agents.campaign.planner import CampaignPlanner, build_subagents
from vista_backend.agents.campaign.subagent import (
    CallableResultParser,
    ParsedResult,
    SubmittedJobInfo,
)
from vista_backend.agents.campaign.wiring import (
    build_collector,
    build_invoke_for_job,
    build_planner_for_job,
    build_status_poll,
    parse_job_state,
)
from vista_backend.config import settings
from vista_backend.db.schemas import ProjectTable
from vista_backend.services import campaign as campaign_service


MANIFEST_YAML = """
domain: testdomain
metrics:
  primary: {name: SCORE, target: 1.0}
subagents:
  - {role: alpha, skill: alpha-skill, job: alpha_job}
  - {role: beta, skill: beta-skill, job: beta_job}
"""


class _FakeHpc:
    async def submit(self, *, job, cluster, node_count, duration, script_args):
        return SubmittedJobInfo(job_id="job-1", cluster=cluster or "odo")

    async def status(self, *, job_id, cluster):
        return "STATE=COMPLETED"

    async def fetch_outputs(self, *, job_id, files, cluster):
        return ""


async def _make_run_step_job(session, alice, *, planner_skill="mock-planner"):
    project = ProjectTable(name=f"wiring-{planner_skill}")
    session.add(project)
    await session.flush()
    run = await campaign_service.create_campaign(
        session, project_id=project.id, user_id=alice.id,
        domain="testdomain", planner_skill=planner_skill,
    )
    step = await campaign_service.add_step(
        session, run_id=run.id, cycle=0, kind="alpha", candidate={"x": 1}
    )
    job = await campaign_service.record_job(
        session, job_id="job-1", step_id=step.id, user_id=alice.id, cluster="odo"
    )
    return project, run, step, job


# --- parse_job_state -------------------------------------------------------

def test_parse_job_state_from_status_output():
    text = "JOB_ID=12345\nCLUSTER=frontier\nSTATE=COMPLETED\n\n--- LOGS ---\nTBR=1.18"
    assert parse_job_state(text) == "COMPLETED"


def test_parse_job_state_defaults_to_unknown():
    assert parse_job_state("no state line here") == "UNKNOWN"
    assert parse_job_state("") == "UNKNOWN"


# --- build_invoke_for_job --------------------------------------------------

@pytest.mark.anyio
async def test_build_invoke_for_job_binds_user_and_paths(session, alice):
    _project, run, _step, job = await _make_run_step_job(session, alice)
    captured = {}

    def fake_builder(user, paths):
        captured["user"] = user
        captured["paths"] = paths

        async def invoke(name, args):
            return "OK"

        return invoke

    invoke = await build_invoke_for_job(session, job, invoke_builder=fake_builder)
    assert await invoke("get_hpc_job_status", {}) == "OK"
    assert captured["user"].email == alice.email
    assert captured["paths"] == project_paths_for(run.project_id, run.user_id)


# --- build_status_poll -----------------------------------------------------

@pytest.mark.anyio
async def test_build_status_poll_derives_invoke_per_job_and_parses(session, alice):
    _project, _run, _step, job = await _make_run_step_job(session, alice)
    calls = []

    def fake_builder(user, paths):
        async def invoke(name, args):
            calls.append((name, args))
            return "STATE=RUNNING\nlogs..."

        return invoke

    poll = build_status_poll(invoke_builder=fake_builder)
    state, raw = await poll(session, job)

    assert state == "RUNNING"
    assert "STATE=RUNNING" in raw
    assert calls == [("get_hpc_job_status", {"job_id": "job-1", "cluster": "odo"})]


# --- build_planner_for_job -------------------------------------------------

@pytest.mark.anyio
async def test_build_planner_for_job_reconstructs_from_skill(session, alice, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _project, run, _step, job = await _make_run_step_job(session, alice)

    # Materialize the planner skill's campaign.yaml in the project's volume skills dir.
    skills_dir = Path(project_paths_for(run.project_id, run.user_id)["skills_dir"])
    (skills_dir / run.planner_skill).mkdir(parents=True)
    (skills_dir / run.planner_skill / "campaign.yaml").write_text(MANIFEST_YAML)

    async def _noop_invoke(name, args):
        return ""

    planner = await build_planner_for_job(
        session, job,
        invoke_builder=lambda user, paths: _noop_invoke,
        parser_factory=lambda d, r: CallableResultParser(lambda **_: ParsedResult(ok=True)),
    )

    assert planner.manifest.roles == ["alpha", "beta"]
    assert set(planner.subagents) == {"alpha", "beta"}


# --- build_collector -------------------------------------------------------

@pytest.mark.anyio
async def test_build_collector_routes_to_run_planner(session, alice):
    manifest = CampaignManifest.model_validate(
        {
            "domain": "d",
            "metrics": {"primary": {"name": "SCORE"}},
            "subagents": [{"role": "alpha", "skill": "alpha-skill", "job": "alpha_job"}],
        }
    )
    subagents = build_subagents(
        manifest, hpc=_FakeHpc(), skills_dir="/unused",
        parser_factory=lambda d, r: CallableResultParser(
            lambda **_: ParsedResult(ok=True, metrics={"SCORE": 9})
        ),
    )
    planner = CampaignPlanner(manifest=manifest, subagents=subagents)

    project = ProjectTable(name="wiring-collector")
    session.add(project)
    await session.flush()
    run = await campaign_service.create_campaign(
        session, project_id=project.id, user_id=alice.id, domain="d", planner_skill="p"
    )
    await planner.dispatch_candidate(
        session, run_id=run.id, user_id=alice.id, candidate={"x": 1}, cycle=0
    )
    job = await campaign_service.get_job(session, "job-1")

    async def provider(_session, _job):
        return planner

    collect = build_collector(provider)
    await collect(session, job, "STATE=COMPLETED")

    step = await campaign_service.get_step(session, job.step_id)
    assert step.status == "completed"
    assert step.result["metrics"]["SCORE"] == 9
