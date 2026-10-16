"""In a Slack turn, side effects wait for Confirm; reads and other channels don't."""

import uuid

import mcp.types
import pytest
from pydantic_ai.messages import FunctionToolResultEvent, ModelResponse, TextPart
from pydantic_ai.toolsets.function import FunctionToolset

from harness import call, make_project, make_user, say, scripted_model, step_model
from vista_backend.agents.agents import (
    McpFormElicitationEvent,
    McpToolApprovalEvent,
    ProjectAgent,
)
from vista_backend.slack import gate
from vista_backend.slack.gate import READ_ONLY, SLACK_INSTRUCTIONS

pytestmark = pytest.mark.anyio


def _agent():
    agent = ProjectAgent(make_project(tools=["*"]), make_user(), uuid.uuid4())
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


async def test_a_side_effect_waits_for_confirm_in_slack():
    agent, toolset, ran = _agent()

    events = await _turn(agent, toolset, "cancel_job", channel="slack", answer="accept")

    (approval,) = _approvals(events)
    assert approval.tool_name == "cancel_job"
    assert ran == ["cancel_job"]


async def test_a_declined_side_effect_does_not_run():
    agent, toolset, ran = _agent()

    events = await _turn(
        agent, toolset, "cancel_job", channel="slack", answer="decline"
    )

    assert ran == []
    assert any("decline" in r for r in _results(events))


async def test_the_app_channel_is_unchanged():
    agent, toolset, ran = _agent()

    events = await _turn(agent, toolset, "cancel_job", channel="app")

    assert ran == ["cancel_job"] and not _approvals(events)


async def test_a_refusal_is_returned_without_asking(monkeypatch):
    agent, toolset, ran = _agent()

    async def refuse(user_id, tool, args):
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
