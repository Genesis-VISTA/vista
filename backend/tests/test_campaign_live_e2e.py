"""
Live-path end-to-end test of the campaign framework over a mock domain.

Unlike test_campaign_e2e (which fakes the planner provider) and test_campaign_driver (which
fakes the planner), this drives the *real* wiring:

  - dispatch through the real campaign agent tools (a FunctionModel planner), building the
    planner from an on-disk mock planner skill (load_manifest + build_subagents + McpHpcTools);
  - the real monitor wiring (build_status_poll + build_collector + build_planner_for_job),
    which reconstructs the planner per job from the DB + on-disk skill (i.e. a restart-style
    resume), then polls → collects → emails.

Only the two true boundaries are faked: the MCP `invoke` (so no real HPC) and the sim-skill
result parser (so no real LLM). Everything else is production code.
"""
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from vista_backend.agents.campaign.agent_tools import CampaignDriverDeps, register_campaign_tools
from vista_backend.agents.campaign.hpc_tools import McpHpcTools
from vista_backend.agents.campaign.manifest import load_manifest
from vista_backend.agents.campaign.mcp_invoke import project_paths_for
from vista_backend.agents.campaign.planner import CampaignPlanner, build_subagents
from vista_backend.agents.campaign.subagent import CallableResultParser, ParsedResult
from vista_backend.agents.campaign.wiring import (
    build_collector,
    build_planner_for_job,
    build_status_poll,
)
from vista_backend.config import settings
from vista_backend.db.schemas import ProjectTable
from vista_backend.services import campaign as campaign_service
from vista_backend.services import chat_session as chat_session_service
from vista_backend.services.campaign_monitor import CampaignMonitor, resume_open_campaigns


MANIFEST_YAML = """
domain: mockdomain
metrics:
  primary: {name: score, target: 1.0}
subagents:
  - {role: neutronics, skill: mock-neutronics, job: neutronics}
  - {role: chemistry, skill: mock-chemistry, job: chemistry}
"""


def _fake_parser_factory(skill_dir, role):
    return CallableResultParser(lambda **_: ParsedResult(ok=True, summary=role, metrics={"role": role}))


def _scripted_planner_llm():
    """Drives intake -> spec -> plan -> dispatch through the real tools, then ends the turn."""
    state = {"run_id": None}

    def driver(messages, info: AgentInfo) -> ModelResponse:
        returns = [
            p for m in messages for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)
        ]
        for r in returns:
            if r.tool_name == "start_campaign" and state["run_id"] is None:
                state["run_id"] = str(r.content).split("run_id=")[1].split()[0]
        step = len(returns)
        rid = state["run_id"]
        if step == 0:
            return ModelResponse(parts=[ToolCallPart("start_campaign", {
                "planner_skill": "mock-planner", "domain": "mockdomain", "title": "Live",
            })])
        if step == 1:
            return ModelResponse(parts=[ToolCallPart("set_campaign_spec", {
                "run_id": rid, "spec": {"platform": "frontier"},
            })])
        if step == 2:
            return ModelResponse(parts=[ToolCallPart("save_campaign_plan", {
                "run_id": rid, "plan": [{"step": 1, "text": "cycle 0"}],
            })])
        if step == 3:
            return ModelResponse(parts=[ToolCallPart("dispatch_cycle", {
                "run_id": rid, "candidates": [{"x": 0.7}], "cycle": 0, "cluster": "frontier",
            })])
        return ModelResponse(parts=[TextPart("Dispatched; awaiting HPC results.")])

    return FunctionModel(driver)


@pytest.mark.anyio
async def test_live_path_dispatch_then_monitor_resume(session, alice, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    project = ProjectTable(name="live-e2e")
    session.add(project)
    await session.flush()
    chat = await chat_session_service.get_or_create_chat_session(
        session, project_id=project.id, user_id=alice.id
    )

    # Materialize the planner skill's campaign.yaml in the session's volume skills dir.
    skills_dir = Path(project_paths_for(chat.id, project.id, alice.id)["skills_dir"])
    (skills_dir / "mock-planner").mkdir(parents=True)
    (skills_dir / "mock-planner" / "campaign.yaml").write_text(MANIFEST_YAML)

    # One fake MCP invoke: submit returns a parseable summary (unique ids); status says done.
    submits = {"n": 0}

    async def the_invoke(name, args):
        if name == "submit_hpc_job":
            submits["n"] += 1
            return f"job_id: job-{submits['n']}\ncluster: frontier\nnodes: 1"
        if name == "get_hpc_job_status":
            return "STATE=COMPLETED"
        return "outputs"

    def fake_invoke_builder(user, paths):
        return the_invoke

    # Dispatch planner: the real construction (manifest from disk + McpHpcTools), fake invoke/parser.
    async def get_planner(_session, run) -> CampaignPlanner:
        paths = project_paths_for(run.session_id, run.project_id, run.user_id)
        manifest = load_manifest(Path(paths["skills_dir"]) / run.planner_skill)
        subagents = build_subagents(
            manifest, hpc=McpHpcTools(the_invoke), skills_dir=paths["skills_dir"],
            parser_factory=_fake_parser_factory,
        )
        return CampaignPlanner(manifest=manifest, subagents=subagents)

    progress: list[str] = []
    deps = CampaignDriverDeps(
        project_id=project.id, user_id=alice.id, session_id=chat.id,
        get_session=lambda: session, get_planner=get_planner, emit_progress=progress.append,
    )
    agent = Agent(model=_scripted_planner_llm())
    register_campaign_tools(agent, deps)

    # --- dispatch via the real agent tools --------------------------------
    result = await agent.run("Run a tritium breeding campaign.")
    assert "awaiting" in result.output.lower()

    runs = await campaign_service.list_campaigns(session, project_id=project.id)
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "running"
    assert run.session_id == chat.id  # recorded so the monitor can find the sandbox
    jobs = await campaign_service.list_jobs_for_run(session, run_id=run.id)
    assert {j.job_name for j in jobs} == {"neutronics", "chemistry"}
    assert len(await campaign_service.list_open_jobs(session)) == 2
    assert any("Dispatched cycle 0" in p for p in progress)

    # While running, the campaign is resumable (a restarted monitor would pick it up).
    assert run.id in {r.id for r in await resume_open_campaigns(session)}

    # --- the real monitor wiring completes the jobs -----------------------
    emails: list[dict] = []

    async def emailer(*, to, subject, body):
        emails.append({"to": to, "subject": subject})
        return True

    async def planner_provider(s, j):
        # The production monitor path: reconstruct the planner per job from the DB + skill.
        return await build_planner_for_job(
            s, j, invoke_builder=fake_invoke_builder, parser_factory=_fake_parser_factory
        )

    monitor = CampaignMonitor(
        poll=build_status_poll(invoke_builder=fake_invoke_builder),
        collect=build_collector(planner_provider),
        send_email=emailer,
    )
    await monitor.reconcile_once(session)

    # Jobs collected + emailed; steps completed.
    steps = await campaign_service.list_steps(session, run_id=run.id)
    assert all(s.status == "completed" for s in steps)
    assert {s.result["metrics"]["role"] for s in steps} == {"neutronics", "chemistry"}
    assert len(await campaign_service.list_open_jobs(session)) == 0
    assert len(emails) == 2
    user_email = (await _user_email(session, alice))
    assert all(e["to"] == user_email for e in emails)

    # --- user confirms exit -> no longer resumable ------------------------
    await campaign_service.set_status(session, run_id=run.id, status="exited")
    assert run.id not in {r.id for r in await resume_open_campaigns(session)}


async def _user_email(session, alice):
    from vista_backend.db.schemas import UserTable

    return (await session.get(UserTable, alice.id)).email
