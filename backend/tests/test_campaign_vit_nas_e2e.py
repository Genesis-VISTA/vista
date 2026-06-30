"""
End-to-end test of the ViT-NAS campaign over a mock HPC boundary, using the REAL
vit-nas-planner manifest + the REAL efficiency scorer.

Like test_campaign_live_e2e (mock domain), but it exercises the shipped vit-nas skill:

  - dispatch through the real campaign agent tools (a FunctionModel planner) building the
    planner from the on-disk vit-nas-planner campaign.yaml;
  - the real monitor wiring collects each finished job — and, via the result_files fix,
    fetches the job's results.json so the parser actually sees the metrics;
  - the shipped score_candidates.py ranks the collected candidates by efficiency.

Faked boundaries only: the MCP invoke (a vit-train-style results.json per job) and the
sim-skill parser (reads that results.json into metrics, as the real LLM parser would).
"""
import importlib.util
import json
import shutil
from pathlib import Path

import pytest
import vista_backend
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from vista_backend.agents.campaign.agent_tools import CampaignDriverDeps, register_campaign_tools
from vista_backend.agents.campaign.hpc_tools import McpHpcTools
from vista_backend.agents.campaign.manifest import load_manifest
from vista_backend.agents.campaign.mcp_invoke import project_paths_for
from vista_backend.agents.campaign.planner import CampaignPlanner, build_subagents
from vista_backend.agents.campaign.subagent import CallableResultParser, ParsedResult
from vista_backend.agents.campaign.wiring import build_collector, build_planner_for_job, build_status_poll
from vista_backend.config import settings
from vista_backend.db.schemas import ProjectTable
from vista_backend.services import campaign as campaign_service
from vista_backend.services import chat_session as chat_session_service
from vista_backend.services.campaign_monitor import CampaignMonitor

_SKILL_SRC = Path(vista_backend.__file__).parent / "db" / "skills" / "vit-nas-planner"

# Two candidates -> job-1, job-2 (in dispatch order). job-1 is the more efficient config.
CANDIDATES = [
    {"embed_dim": 1024, "depth": 12, "num_heads": 8, "patch_size": 8,
     "lr": 5e-4, "global_batch_size": 16, "tensor_parallel": 1, "context_parallel": 1},
    {"embed_dim": 2048, "depth": 12, "num_heads": 16, "patch_size": 8,
     "lr": 1e-4, "global_batch_size": 16, "tensor_parallel": 2, "context_parallel": 1},
]
RESULTS_BY_JOB = {
    "job-1": {"metrics": {"val_loss": 0.42, "throughput_samples_s": 85.0}},   # efficiency 202.4
    "job-2": {"metrics": {"val_loss": 0.60, "throughput_samples_s": 100.0}},  # efficiency 166.7
}


def _load_scorer():
    spec = importlib.util.spec_from_file_location("vit_scorer", _SKILL_SRC / "scripts" / "score_candidates.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _vit_parser_factory(skill_dir, role):
    """Parse a vit-train results.json (the fetched output) into metrics — proving collect fetched it."""
    def parse(*, candidate, raw_status, raw_outputs):
        data = json.loads(raw_outputs)
        metrics = data.get("metrics", {})
        ok = metrics.get("val_loss") is not None and metrics.get("throughput_samples_s") is not None
        return ParsedResult(ok=ok, summary=role, metrics=metrics)
    return CallableResultParser(parse)


def _scripted_planner_llm():
    """Drive intake -> spec -> plan -> dispatch (2 candidates) through the real tools, then end."""
    state = {"run_id": None}

    def driver(messages, info: AgentInfo) -> ModelResponse:
        returns = [p for m in messages for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)]
        for r in returns:
            if r.tool_name == "start_campaign" and state["run_id"] is None:
                state["run_id"] = str(r.content).split("run_id=")[1].split()[0]
        step = len(returns)
        rid = state["run_id"]
        if step == 0:
            return ModelResponse(parts=[ToolCallPart("start_campaign", {
                "planner_skill": "vit-nas-planner", "domain": "vit-nas", "title": "ViT-NAS",
            })])
        if step == 1:
            return ModelResponse(parts=[ToolCallPart("set_campaign_spec", {
                "run_id": rid, "spec": {"platform": "frontier", "efficiency_target": 185},
            })])
        if step == 2:
            return ModelResponse(parts=[ToolCallPart("save_campaign_plan", {
                "run_id": rid, "plan": [{"step": 1, "text": "cycle 0: two configs"}],
            })])
        if step == 3:
            return ModelResponse(parts=[ToolCallPart("dispatch_cycle", {
                "run_id": rid, "candidates": CANDIDATES, "cycle": 0, "cluster": "frontier",
            })])
        return ModelResponse(parts=[TextPart("Dispatched; awaiting training results.")])

    return FunctionModel(driver)


@pytest.mark.anyio
async def test_vit_nas_dispatch_collect_score(session, alice, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    project = ProjectTable(name="vit-nas-e2e")
    session.add(project)
    await session.flush()
    chat = await chat_session_service.get_or_create_chat_session(
        session, project_id=project.id, user_id=alice.id
    )

    # Materialize the REAL vit-nas-planner manifest in the session's volume skills dir.
    skills_dir = Path(project_paths_for(chat.id, project.id, alice.id)["skills_dir"])
    dst = skills_dir / "vit-nas-planner"
    dst.mkdir(parents=True)
    shutil.copy(_SKILL_SRC / "campaign.yaml", dst / "campaign.yaml")

    submits = {"n": 0}

    async def the_invoke(name, args):
        if name == "submit_hpc_job":
            submits["n"] += 1
            return f"job_id: job-{submits['n']}\ncluster: frontier\nnodes: 1"
        if name == "get_hpc_job_status":
            return "STATE=COMPLETED"
        if name == "get_hpc_job_outputs":
            return json.dumps(RESULTS_BY_JOB[args["job_id"]])
        return "outputs"

    def fake_invoke_builder(user, paths):
        return the_invoke

    async def get_planner(_session, run) -> CampaignPlanner:
        paths = project_paths_for(run.session_id, run.project_id, run.user_id)
        manifest = load_manifest(Path(paths["skills_dir"]) / run.planner_skill)
        subagents = build_subagents(
            manifest, hpc=McpHpcTools(the_invoke), skills_dir=paths["skills_dir"],
            parser_factory=_vit_parser_factory,
        )
        return CampaignPlanner(manifest=manifest, subagents=subagents)

    deps = CampaignDriverDeps(
        project_id=project.id, user_id=alice.id, session_id=chat.id,
        get_session=lambda: session, get_planner=get_planner,
    )
    agent = Agent(model=_scripted_planner_llm())
    register_campaign_tools(agent, deps)

    # --- dispatch via the real agent tools --------------------------------
    result = await agent.run("Search ViT configs for the best training efficiency.")
    assert "awaiting" in result.output.lower()

    run = (await campaign_service.list_campaigns(session, project_id=project.id))[0]
    assert run.status == "running"
    jobs = await campaign_service.list_jobs_for_run(session, run_id=run.id)
    assert {j.job_name for j in jobs} == {"vit-train"}  # single training role
    assert len(jobs) == 2 and len(await campaign_service.list_open_jobs(session)) == 2

    # --- the real monitor collects each job, fetching results.json --------
    async def planner_provider(s, j):
        return await build_planner_for_job(
            s, j, invoke_builder=fake_invoke_builder, parser_factory=_vit_parser_factory
        )

    monitor = CampaignMonitor(
        poll=build_status_poll(invoke_builder=fake_invoke_builder),
        collect=build_collector(planner_provider),
        send_email=lambda **_: _ok(),
    )
    await monitor.reconcile_once(session)

    steps = await campaign_service.list_steps(session, run_id=run.id)
    assert all(s.status == "completed" for s in steps)
    # The collector fetched results.json -> the parser read real metrics onto each step.
    by_loss = {s.result["metrics"]["val_loss"] for s in steps}
    assert by_loss == {0.42, 0.60}
    assert len(await campaign_service.list_open_jobs(session)) == 0

    # --- score the collected cycle with the SHIPPED scorer ----------------
    scorer = _load_scorer()
    scored = scorer.score_candidates(
        [{"params": s.candidate, "metrics": s.result["metrics"]} for s in steps],
        efficiency_target=185,
    )
    assert round(scored["best"]["efficiency"], 1) == 202.4   # the val_loss=0.42 config
    assert scored["best"]["metrics"]["val_loss"] == 0.42
    assert scored["target_met"] is True
    assert scored["infeasible"] == []


async def _ok():
    return True
