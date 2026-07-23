"""
Drive the conversational campaign tools with a PydanticAI FunctionModel.

A scripted "planner LLM" calls start_campaign -> set_campaign_spec -> save_campaign_plan ->
dispatch_cycle -> finish_campaign, threading the run_id from start_campaign's return. Proves
the whole tool flow + state transitions + progress emission deterministically, with no real
LLM, MCP, or HPC (the planner + HPC boundary are faked).
"""

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from vista_backend.agents.campaign.agent_tools import (
    CampaignDriverDeps,
    register_campaign_tools,
)
from vista_backend.agents.campaign.manifest import CampaignManifest
from vista_backend.agents.campaign.planner import CampaignPlanner, build_subagents
from vista_backend.agents.campaign.subagent import (
    CallableResultParser,
    ParsedResult,
    SubmittedJobInfo,
)
from vista_backend.db.schemas import ProjectTable
from vista_backend.services import campaign as campaign_service
from vista_backend.services import chat_session as chat_session_service


class _FakeHpc:
    def __init__(self):
        self.n = 0

    async def submit(self, *, job, cluster, node_count, duration, script_args):
        self.n += 1
        return SubmittedJobInfo(job_id=f"job-{self.n}", cluster=cluster or "frontier")

    async def status(self, *, job_id, cluster):
        return "STATE=COMPLETED"

    async def fetch_outputs(self, *, job_id, files, cluster):
        return ""


def _fake_planner() -> CampaignPlanner:
    manifest = CampaignManifest.model_validate(
        {
            "domain": "mockdomain",
            "metrics": {"primary": {"name": "score", "target": 1.0}},
            "subagents": [
                {"role": "neutronics", "skill": "mock-neutronics", "job": "neutronics"},
                {"role": "chemistry", "skill": "mock-chemistry", "job": "chemistry"},
            ],
        }
    )
    subagents = build_subagents(
        manifest,
        hpc=_FakeHpc(),
        skills_dir="/unused",
        parser_factory=lambda d, r: CallableResultParser(
            lambda **_: ParsedResult(ok=True)
        ),
    )
    return CampaignPlanner(manifest=manifest, subagents=subagents)


def _scripted_planner_llm():
    """A FunctionModel that walks the campaign tools in order, threading the run_id."""
    state = {"run_id": None}

    def driver(messages, info: AgentInfo) -> ModelResponse:
        returns = [
            p
            for m in messages
            for p in getattr(m, "parts", [])
            if isinstance(p, ToolReturnPart)
        ]
        for r in returns:
            if r.tool_name == "start_campaign" and state["run_id"] is None:
                state["run_id"] = str(r.content).split("run_id=")[1].split()[0]

        step = len(returns)
        rid = state["run_id"]
        if step == 0:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "start_campaign",
                        {
                            "planner_skill": "mock-planner",
                            "domain": "mockdomain",
                            "title": "Mock sweep",
                        },
                    )
                ]
            )
        if step == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "set_campaign_spec",
                        {
                            "run_id": rid,
                            "spec": {"platform": "frontier", "tbr_target": 1.1},
                        },
                    )
                ]
            )
        if step == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "save_campaign_plan",
                        {
                            "run_id": rid,
                            "plan": [{"step": 1, "text": "cycle 0"}],
                        },
                    )
                ]
            )
        if step == 3:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "dispatch_cycle",
                        {
                            "run_id": rid,
                            "candidates": [{"x": 0.7}],
                            "cycle": 0,
                            "cluster": "frontier",
                        },
                    )
                ]
            )
        if step == 4:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "finish_campaign",
                        {
                            "run_id": rid,
                            "status": "exited",
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("Campaign complete.")])

    return FunctionModel(driver)


@pytest.mark.anyio
async def test_function_model_drives_full_campaign(session, alice):
    project = ProjectTable(name="driver-project")
    session.add(project)
    await session.flush()
    chat = await chat_session_service.get_or_create_chat_session(
        session, project_id=project.id, user_id=alice.id
    )

    progress: list[str] = []
    deps = CampaignDriverDeps(
        project_id=project.id,
        user_id=alice.id,
        session_id=chat.id,
        get_session=lambda: session,
        get_planner=lambda _s, _run: _fake_planner_async(),
        emit_progress=progress.append,
    )

    agent = Agent(model=_scripted_planner_llm())
    register_campaign_tools(agent, deps)

    result = await agent.run("Run a tritium breeding campaign.")
    assert "complete" in result.output.lower()

    # A single campaign was created, driven, and closed.
    runs = await campaign_service.list_campaigns(session, project_id=project.id)
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "exited"
    assert (
        run.session_id == chat.id
    )  # campaign is tied to its conversation (multi-session)
    assert run.spec["tbr_target"] == 1.1
    assert run.plan[0]["text"] == "cycle 0"

    # The cycle dispatched a step + job per subagent role.
    steps = await campaign_service.list_steps(session, run_id=run.id)
    assert {s.kind for s in steps} == {"neutronics", "chemistry"}
    assert all(s.status == "dispatched" for s in steps)
    jobs = await campaign_service.list_jobs_for_run(session, run_id=run.id)
    assert len(jobs) == 2

    # Progress was streamed during dispatch.
    assert any("Dispatched cycle 0" in p for p in progress)


async def _fake_planner_async() -> CampaignPlanner:
    return _fake_planner()


@pytest.mark.anyio
async def test_tools_reject_run_from_another_project(session, alice, bob):
    # A campaign owned by bob in his own project...
    bob_project = ProjectTable(name="bob-driver")
    session.add(bob_project)
    await session.flush()
    other = await campaign_service.create_campaign(
        session,
        project_id=bob_project.id,
        user_id=bob.id,
        domain="mockdomain",
        planner_skill="mock-planner",
    )

    # ...is not addressable from alice's project's tools (access boundary).
    alice_project = ProjectTable(name="alice-driver")
    session.add(alice_project)
    await session.flush()

    captured = {}

    def driver(messages, info: AgentInfo) -> ModelResponse:
        # A cross-project run raises ModelRetry, which appears as a RetryPromptPart.
        retries = [
            p
            for m in messages
            for p in getattr(m, "parts", [])
            if isinstance(p, RetryPromptPart)
        ]
        if retries:
            captured["retry"] = str(retries[-1].content)
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(
            parts=[ToolCallPart("get_campaign_status", {"run_id": str(other.id)})]
        )

    deps = CampaignDriverDeps(
        project_id=alice_project.id,
        user_id=alice.id,
        session_id=None,
        get_session=lambda: session,
        get_planner=lambda _s, _r: _fake_planner_async(),
    )
    agent = Agent(model=FunctionModel(driver))
    register_campaign_tools(agent, deps)

    await agent.run("status?")
    # The tool raised ModelRetry because the run isn't in alice's project.
    assert "not found in this project" in captured["retry"]


@pytest.mark.anyio
async def test_start_campaign_requires_a_chat_session(session, alice):
    # Multi-session: a stateless run (session_id=None) can't host a durable, resumable campaign.
    project = ProjectTable(name="stateless-driver")
    session.add(project)
    await session.flush()

    captured = {}

    def driver(messages, info: AgentInfo) -> ModelResponse:
        retries = [
            p
            for m in messages
            for p in getattr(m, "parts", [])
            if isinstance(p, RetryPromptPart)
        ]
        if retries:
            captured["retry"] = str(retries[-1].content)
            return ModelResponse(parts=[TextPart("ok")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "start_campaign",
                    {
                        "planner_skill": "mock-planner",
                        "domain": "mockdomain",
                    },
                )
            ]
        )

    deps = CampaignDriverDeps(
        project_id=project.id,
        user_id=alice.id,
        session_id=None,
        get_session=lambda: session,
        get_planner=lambda _s, _r: _fake_planner_async(),
    )
    agent = Agent(model=FunctionModel(driver))
    register_campaign_tools(agent, deps)

    await agent.run("run a campaign")
    assert "active conversation" in captured["retry"]
    # No campaign was created in a stateless run.
    assert await campaign_service.list_campaigns(session, project_id=project.id) == []
