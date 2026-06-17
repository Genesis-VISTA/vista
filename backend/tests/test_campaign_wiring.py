"""Tests for the monitor<->MCP/planner wiring seams."""
import pytest

from vista_backend.agents.campaign.manifest import CampaignManifest
from vista_backend.agents.campaign.planner import CampaignPlanner, build_subagents
from vista_backend.agents.campaign.subagent import (
    CallableResultParser,
    ParsedResult,
    SubmittedJobInfo,
)
from vista_backend.agents.campaign.wiring import (
    build_collector,
    build_status_poll,
    parse_job_state,
)
from vista_backend.db.schemas import ProjectTable
from vista_backend.services import campaign as campaign_service


def test_parse_job_state_from_status_output():
    text = "JOB_ID=12345\nCLUSTER=frontier\nSTATE=COMPLETED\n\n--- LOGS ---\nTBR=1.18"
    assert parse_job_state(text) == "COMPLETED"


def test_parse_job_state_defaults_to_unknown():
    assert parse_job_state("no state line here") == "UNKNOWN"
    assert parse_job_state("") == "UNKNOWN"


@pytest.mark.anyio
async def test_build_status_poll_invokes_status_tool_and_parses():
    calls = []

    async def invoke(tool, args):
        calls.append((tool, args))
        return "STATE=RUNNING\nlogs..."

    poll = build_status_poll(invoke)

    class _Job:
        job_id = "j-1"
        cluster = "odo"

    state, raw = await poll(_Job())
    assert state == "RUNNING"
    assert "STATE=RUNNING" in raw
    assert calls == [("get_hpc_job_status", {"job_id": "j-1", "cluster": "odo"})]


class _FakeHpc:
    async def submit(self, *, job, cluster, node_count, duration, script_args):
        return SubmittedJobInfo(job_id="job-1", cluster=cluster or "odo")

    async def status(self, *, job_id, cluster):
        return "STATE=COMPLETED"

    async def fetch_outputs(self, *, job_id, files, cluster):
        return ""


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

    project = ProjectTable(name="wiring-project")
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
