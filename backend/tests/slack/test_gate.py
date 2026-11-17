"""In a Slack turn, a job submission waits for Confirm; other tools and channels don't."""

import uuid

import mcp.types
import pytest
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.toolsets.function import FunctionToolset

from harness import (
    call,
    make_local_settings,
    make_project,
    say,
    scripted_model,
    step_model,
)
from vista_backend.agents.agents import (
    McpFormElicitationEvent,
    McpToolApprovalEvent,
    ProjectAgent,
)
from vista_backend.slack import gate
from vista_backend.slack.gate import READ_ONLY, SLACK_INSTRUCTIONS

pytestmark = pytest.mark.anyio


def _agent():
    agent = ProjectAgent(make_project(tools=["*"]), make_local_settings(), uuid.uuid4())
    ran: list[str] = []
    toolset = FunctionToolset()

    @toolset.tool_plain(metadata=READ_ONLY)
    async def job_status() -> str:
        """Read a job's state."""
        ran.append("job_status")
        return "RUNNING"

    @toolset.tool_plain
    async def cancel_job() -> str:
        """Cancel a job."""
        ran.append("cancel_job")
        return "cancelled"

    @toolset.tool_plain(name="vista__submit_hpc_job")
    async def submit() -> str:
        """Submit a job."""
        ran.append("submit")
        return "job_id: 4471"

    @toolset.tool_plain(metadata=READ_ONLY)
    async def ask() -> str:
        """A tool whose MCP server asks the user a form question."""
        callback = agent._cur_mcp_elicitation_callback
        assert callback is not None
        params = mcp.types.ElicitRequestFormParams(
            message="Lux password?",
            requestedSchema={"type": "object", "properties": {}},
        )
        result = await callback(None, params)  # type: ignore[arg-type]
        return f"user action: {result.action}"

    return agent, toolset, ran


async def _turn(agent, toolset, tool, *, channel, answer=None):
    events = []
    model = step_model([call(tool), say("done")])
    with agent.agent.override(model=model, toolsets=[toolset]):
        async for event in agent.run_stream(
            user_prompt="go", enable_elicitation=True, channel=channel
        ):
            events.append(event)
            if isinstance(event, McpToolApprovalEvent) and answer is not None:
                agent.resolve_elicitation(event.elicitation_id, answer)
    return events


def _approvals(events):
    return [e for e in events if isinstance(e, McpToolApprovalEvent)]


def _results(events):
    return [
        str(getattr(e.part, "content", ""))
        for e in events
        if isinstance(e, FunctionToolResultEvent)
    ]


async def test_a_read_only_tool_runs_unconfirmed_in_slack():
    agent, toolset, ran = _agent()

    events = await _turn(agent, toolset, "job_status", channel="slack")

    assert ran == ["job_status"] and not _approvals(events)


async def test_a_submission_waits_for_confirm_in_slack():
    agent, toolset, ran = _agent()

    events = await _turn(
        agent, toolset, "vista__submit_hpc_job", channel="slack", answer="accept"
    )

    (approval,) = _approvals(events)
    assert approval.tool_name == "vista__submit_hpc_job"
    assert ran == ["submit"]


async def test_other_side_effects_run_unconfirmed_in_slack():
    agent, toolset, ran = _agent()

    events = await _turn(agent, toolset, "cancel_job", channel="slack")

    assert ran == ["cancel_job"] and not _approvals(events)


async def test_a_declined_submission_does_not_run():
    agent, toolset, ran = _agent()

    events = await _turn(
        agent, toolset, "vista__submit_hpc_job", channel="slack", answer="decline"
    )

    assert ran == []
    assert any("decline" in r for r in _results(events))


async def test_the_app_channel_is_unchanged():
    agent, toolset, ran = _agent()

    events = await _turn(agent, toolset, "cancel_job", channel="app")

    assert ran == ["cancel_job"] and not _approvals(events)


async def test_a_refusal_stops_any_side_effect_without_asking(monkeypatch):
    agent, toolset, ran = _agent()

    async def refuse(tool, args, **_):
        return "Slack is locked. Unlock it from the VISTA desktop app."

    monkeypatch.setattr(gate, "_refusal", refuse)

    events = await _turn(agent, toolset, "cancel_job", channel="slack", answer="accept")

    assert ran == [] and not _approvals(events)
    assert any("Slack is locked" in r for r in _results(events))


async def test_forms_are_declined_in_slack_and_never_shown():
    agent, toolset, _ = _agent()

    events = await _turn(agent, toolset, "ask", channel="slack")

    assert not [e for e in events if isinstance(e, McpFormElicitationEvent)]
    assert any("user action: decline" in r for r in _results(events))


async def test_slack_turns_get_slack_instructions_and_app_turns_dont():
    agent, toolset, _ = _agent()
    seen: list[str] = []

    def model(messages, info):
        seen.append(messages[-1].instructions or "")
        return ModelResponse(parts=[TextPart("ok")])

    for channel in ["slack", "app"]:
        with agent.agent.override(model=scripted_model(model), toolsets=[toolset]):
            async for _ in agent.run_stream(user_prompt="hi", channel=channel):
                pass

    assert SLACK_INSTRUCTIONS.strip() in seen[0]
    assert "Slack" not in seen[1]


# ---------------------------------------------------------------------------
# Group 5: what is confirmed, string arguments, and choices
# ---------------------------------------------------------------------------


def _tool_agent(name: str):
    """An agent with one write tool called `name`, recording whether it ran."""
    agent = ProjectAgent(make_project(tools=["*"]), make_local_settings(), uuid.uuid4())
    ran: list[str] = []
    toolset = FunctionToolset()

    @toolset.tool_plain(name=name)
    async def tool(job_id: str = "", app_type: str = "") -> str:
        """A side effect on the cluster."""
        ran.append(name)
        return "done"

    return agent, toolset, ran


@pytest.mark.parametrize(
    "name",
    [
        "vista__submit_hpc_job",
        "vista__cancel_hpc_job",
        "vista__agenthpc_submit_parameter_set",
        "vista__agenthpc_cancel_job",
        "vista__agenthpc_cancel_all_pending",
    ],
)
async def test_submissions_and_cancellations_wait_for_confirm(name):
    agent, toolset, ran = _tool_agent(name)

    events = await _turn(agent, toolset, name, channel="slack", answer="decline")

    assert [a.tool_name for a in _approvals(events)] == [name]
    assert ran == [], "declined, so it never ran"


async def test_string_arguments_reach_the_card():
    """OpenAI-compatible providers send arguments as a JSON string (the blank card)."""
    agent, toolset, _ = _tool_agent("vista__cancel_hpc_job")
    events = []
    step = ModelResponse(
        parts=[ToolCallPart("vista__cancel_hpc_job", '{"job_id": "4471"}')]
    )
    with agent.agent.override(
        model=step_model([step, say("done")]), toolsets=[toolset]
    ):
        async for event in agent.run_stream(
            user_prompt="go", enable_elicitation=True, channel="slack"
        ):
            events.append(event)
            if isinstance(event, McpToolApprovalEvent):
                agent.resolve_elicitation(event.elicitation_id, "decline")

    (approval,) = _approvals(events)
    assert approval.args == {"job_id": "4471"}


def test_args_dict():
    assert gate.args_dict('{"a": 1}') == {"a": 1}
    assert gate.args_dict("") == {}
    assert gate.args_dict("not json") is None
    assert gate.args_dict({"a": 1}) == {"a": 1}


def _asking_agent(params):
    agent = ProjectAgent(make_project(tools=["*"]), make_local_settings(), uuid.uuid4())
    toolset = FunctionToolset()

    @toolset.tool_plain(metadata=READ_ONLY)
    async def ask() -> str:
        """A tool whose MCP server asks the researcher something."""
        callback = agent._cur_mcp_elicitation_callback
        assert callback is not None
        result = await callback(None, params)  # type: ignore[arg-type]
        return f"user action: {result.action} {result.content}"

    return agent, toolset


async def test_a_choice_form_is_shown_in_slack_and_answered():
    """A transfer's Approve/Decline, as FastMCP sends a scalar choice."""
    agent, toolset = _asking_agent(
        mcp.types.ElicitRequestFormParams(
            message="Download chart.png from Odo?",
            requestedSchema={
                "type": "object",
                "properties": {
                    "value": {"type": "string", "enum": ["Approve", "Decline"]}
                },
                "required": ["value"],
            },
        )
    )
    events = []
    with agent.agent.override(
        model=step_model([call("ask"), say("done")]), toolsets=[toolset]
    ):
        async for event in agent.run_stream(
            user_prompt="go", enable_elicitation=True, channel="slack"
        ):
            events.append(event)
            if isinstance(event, McpFormElicitationEvent):
                agent.resolve_elicitation(
                    event.elicitation_id, "accept", {"value": "Approve"}
                )

    assert [e for e in events if isinstance(e, McpFormElicitationEvent)]
    assert any("accept" in r and "Approve" in r for r in _results(events))


async def test_a_url_is_declined_in_slack_and_never_shown():
    agent, toolset = _asking_agent(
        mcp.types.ElicitRequestURLParams(
            mode="url",
            message="Log in to Globus",
            url="https://auth.globus.org/",
            elicitationId="u1",
        )
    )
    events = await _turn(agent, toolset, "ask", channel="slack")
    assert not [e for e in events if type(e).__name__ == "McpUrlElicitationEvent"]
    assert any("user action: decline" in r for r in _results(events))
