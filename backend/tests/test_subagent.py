"""Tests for the generic subagent runtime (dispatch/collect + skill-specialized prompting).

HPC tools and the result parser are injected, so these run with no real HPC, MCP, or LLM.
"""
import pytest

from vista_backend.agents.campaign.subagent import (
    AgentResultParser,
    CallableResultParser,
    ParsedResult,
    SubAgent,
    SubAgentOrder,
    SubmittedJobInfo,
    build_parse_user_prompt,
    build_skill_parser,
    build_subagent_system_prompt,
)
from vista_backend.db.schemas import ProjectTable
from vista_backend.services import campaign as campaign_service


MOCK_SKILL_MD = """---
name: mock-neutronics
description: Parse a mock neutronics job's TBR from its log.
---

# Mock neutronics skill

Read the line `TBR=<value>` from the job log and report it as the `TBR` metric.
"""


class FakeHpcTools:
    """In-memory HpcTools: records the submit call and returns canned status/outputs."""
    def __init__(self):
        self.submitted: dict | None = None

    async def submit(self, *, job, cluster, node_count, duration, script_args):
        self.submitted = {
            "job": job, "cluster": cluster, "node_count": node_count,
            "duration": duration, "script_args": script_args,
        }
        return SubmittedJobInfo(
            job_id="job-1", cluster=cluster or "frontier",
            log_path="/o/log-job-1.out", output_dir="/o/job-1",
        )

    async def status(self, *, job_id, cluster):
        return f"STATE=COMPLETED\nTBR=1.18 for {job_id}"

    async def fetch_outputs(self, *, job_id, files, cluster):
        return f"downloaded: {files}"


async def _make_run_and_step(session, alice, *, kind="neutronics"):
    project = ProjectTable(name=f"subagent-{kind}")
    session.add(project)
    await session.flush()
    run = await campaign_service.create_campaign(
        session, project_id=project.id, user_id=alice.id,
        domain="splash", planner_skill="splash-planner",
    )
    step = await campaign_service.add_step(
        session, run_id=run.id, cycle=0, kind=kind, candidate={"li6": 0.7}
    )
    return run, step


@pytest.mark.anyio
async def test_dispatch_records_job_and_marks_step(session, alice):
    run, step = await _make_run_and_step(session, alice)
    hpc = FakeHpcTools()
    parser = CallableResultParser(lambda **_: ParsedResult(ok=True))
    agent = SubAgent(role="neutronics", hpc=hpc, parser=parser)

    result = await agent.dispatch(
        session,
        step=step,
        user_id=alice.id,
        order=SubAgentOrder(job="neutronics", candidate={"li6": 0.7}, cluster="frontier"),
    )

    assert result.job_ids == ["job-1"]
    assert hpc.submitted["job"] == "neutronics"

    job = await campaign_service.get_job(session, "job-1")
    assert job is not None
    assert job.step_id == step.id
    assert job.user_id == alice.id
    assert job.cluster == "frontier"
    assert job.log_path == "/o/log-job-1.out"

    refreshed = await campaign_service.get_step(session, step.id)
    assert refreshed.status == "dispatched"


@pytest.mark.anyio
async def test_collect_parses_completes_step_and_marks_job(session, alice):
    run, step = await _make_run_and_step(session, alice)
    hpc = FakeHpcTools()

    async def parse(*, candidate, raw_status, raw_outputs):
        # Trivially extract TBR from the canned status to prove the candidate + raw text flow through.
        tbr = float(raw_status.split("TBR=")[1].split()[0])
        return ParsedResult(ok=True, summary=f"li6={candidate['li6']}", metrics={"TBR": tbr})

    agent = SubAgent(role="neutronics", hpc=hpc, parser=CallableResultParser(parse))
    await agent.dispatch(
        session, step=step, user_id=alice.id,
        order=SubAgentOrder(job="neutronics", candidate={"li6": 0.7}, cluster="frontier"),
    )
    job = await campaign_service.get_job(session, "job-1")

    parsed = await agent.collect(session, job=job, files=["result.json"])

    assert parsed.metrics["TBR"] == 1.18
    assert parsed.summary == "li6=0.7"

    refreshed_step = await campaign_service.get_step(session, step.id)
    assert refreshed_step.status == "completed"
    assert refreshed_step.result["metrics"]["TBR"] == 1.18

    refreshed_job = await campaign_service.get_job(session, "job-1")
    assert refreshed_job.result_collected is True
    assert refreshed_job.job_id not in {
        j.job_id for j in await campaign_service.list_open_jobs(session)
    }


@pytest.mark.anyio
async def test_collect_marks_step_failed_when_parse_not_ok(session, alice):
    run, step = await _make_run_and_step(session, alice)
    hpc = FakeHpcTools()
    parser = CallableResultParser(lambda **_: ParsedResult(ok=False, summary="no outputs"))
    agent = SubAgent(role="neutronics", hpc=hpc, parser=parser)
    await agent.dispatch(
        session, step=step, user_id=alice.id,
        order=SubAgentOrder(job="neutronics", cluster="odo"),
    )
    job = await campaign_service.get_job(session, "job-1")

    await agent.collect(session, job=job)

    refreshed = await campaign_service.get_step(session, step.id)
    assert refreshed.status == "failed"


def test_build_subagent_system_prompt_inlines_skill(tmp_path):
    skill_dir = tmp_path / "mock-neutronics"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(MOCK_SKILL_MD)

    prompt = build_subagent_system_prompt(skill_dir, role="neutronics")
    assert "neutronics subagent" in prompt
    assert "Mock neutronics skill" in prompt
    assert "TBR=<value>" in prompt  # the skill body is inlined, not just referenced


def test_build_skill_parser_constructs_specialized_parser(tmp_path):
    skill_dir = tmp_path / "mock-neutronics"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(MOCK_SKILL_MD)

    parser = build_skill_parser(skill_dir, role="neutronics")
    assert isinstance(parser, AgentResultParser)
    assert "Mock neutronics skill" in parser.system_prompt  # skill drives the prompt


def test_build_parse_user_prompt_includes_inputs():
    prompt = build_parse_user_prompt(
        candidate={"li6": 0.7}, raw_status="STATE=COMPLETED", raw_outputs="result.json"
    )
    assert "li6" in prompt
    assert "STATE=COMPLETED" in prompt
    assert "result.json" in prompt
