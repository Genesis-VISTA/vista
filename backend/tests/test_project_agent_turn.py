"""
One-turn `ProjectAgent` component tests (testing roadmap Milestone B).

Turns run end to end — scripted model, real agent loop, real event stream —
with only the MCP boundary faked. `run_stream` does not require entering the
agent, which is what keeps the STDIO / HTTP MCP servers out of these tests.

VISTAGuard gates stay out of scope; the elicitation and approval tests drive
only the event plumbing that exists without the sidecar.
"""

import asyncio
import uuid

import mcp.types
import pytest
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelResponse,
    TextPart,
)
from pydantic_ai.models.function import AgentInfo
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.usage import UsageLimitExceeded

from harness import (
    agent_under_test,
    always_calls,
    call,
    make_project,
    make_user,
    say,
    scripted_model,
    step_model,
)
from vista_backend.agents.agents import (
    LogEvent,
    McpFormElicitationEvent,
    McpToolApprovalEvent,
    McpUrlElicitationEvent,
    ProjectAgent,
    ProjectAgentResultEvent,
)

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Happy path: tool call -> final answer
# ---------------------------------------------------------------------------


async def test_turn_calls_tool_then_answers():
    project = make_project(tools=["*"], knowledge_bases=["msre-reports"])
    model = step_model(
        [
            call("rag_search", query="FLiBe viscosity", kb_slug="msre-reports"),
            say("FLiBe viscosity falls with temperature."),
        ]
    )

    with agent_under_test(project, make_user(), model) as (agent, mcp_fake):
        events = [
            e async for e in agent.run_stream(user_prompt="How viscous is FLiBe?")
        ]

    assert mcp_fake.names_called() == ["rag_search"]
    assert mcp_fake.args_for("rag_search")["kb_slug"] == "msre-reports"

    results = [e for e in events if isinstance(e, ProjectAgentResultEvent)]
    assert len(results) == 1
    result = results[0].result
    assert result.usage.requests == 2
    assert result.usage.tool_calls == 1
    assert any(
        isinstance(p, TextPart) and "FLiBe viscosity falls" in p.content
        for m in result.new_messages
        for p in getattr(m, "parts", [])
    )


async def test_turn_emits_tool_call_and_result_events():
    project = make_project(tools=["*"], knowledge_bases=["msre-reports"])
    model = step_model([call("rag_search", query="salts"), say("done")])

    with agent_under_test(project, make_user(), model) as (agent, _):
        events = [e async for e in agent.run_stream(user_prompt="hi")]

    assert any(isinstance(e, FunctionToolCallEvent) for e in events)
    assert any(isinstance(e, FunctionToolResultEvent) for e in events)
    assert isinstance(events[-1], ProjectAgentResultEvent)


async def test_turn_logs_each_tool_call():
    project = make_project(tools=["*"])
    model = step_model([call("run_bash", command="echo hi"), say("done")])

    with agent_under_test(project, make_user(), model) as (agent, _):
        events = [e async for e in agent.run_stream(user_prompt="hi")]

    logs = [e for e in events if isinstance(e, LogEvent)]
    assert any(log.area == "Tool:run_bash" and "Called" in log.message for log in logs)
    assert any(
        log.area == "Tool:run_bash" and "completed" in log.message for log in logs
    )
    assert any(log.area == "Agent" and "Turn completed" in log.message for log in logs)


async def test_result_logs_match_the_streamed_log_events():
    project = make_project(tools=["*"])
    model = step_model([call("run_bash", command="echo hi"), say("done")])

    with agent_under_test(project, make_user(), model) as (agent, _):
        events = [e async for e in agent.run_stream(user_prompt="hi")]

    result = next(e for e in events if isinstance(e, ProjectAgentResultEvent)).result
    streamed = [e.message for e in events if isinstance(e, LogEvent)]
    # The result carries the run's log history; the final "Turn completed"
    # line is emitted alongside the result itself.
    assert [entry.message for entry in result.logs] == streamed


async def test_denied_tool_is_unavailable_to_a_model_that_asks_for_it():
    """A model that names a denied tool gets a retry prompt, not an invocation."""
    project = make_project(tools=["*", "!run_bash"])
    model = step_model([call("run_bash", command="rm -rf /"), say("could not")])

    with agent_under_test(project, make_user(), model) as (agent, mcp_fake):
        events = [e async for e in agent.run_stream(user_prompt="delete everything")]

    assert "run_bash" not in mcp_fake.names_called()
    assert any(isinstance(e, ProjectAgentResultEvent) for e in events)


async def test_run_returns_the_same_result_as_the_stream():
    project = make_project(tools=["*"])
    model = step_model(
        [call("display_file", uri="/mnt/data/output/plot.png"), say("here")]
    )

    with agent_under_test(project, make_user(), model) as (agent, mcp_fake):
        result = await agent.run(user_prompt="show me the plot")

    assert mcp_fake.names_called() == ["display_file"]
    assert result.usage.tool_calls == 1


async def test_message_history_is_replayed_to_the_model():
    project = make_project(tools=["*"])
    first = step_model([say("first answer")])
    with agent_under_test(project, make_user(), first) as (agent, _):
        prior = (await agent.run(user_prompt="first question")).new_messages

    seen: list[int] = []

    def driver(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(len(messages))
        return ModelResponse(parts=[TextPart("second answer")])

    with agent_under_test(project, make_user(), scripted_model(driver)) as (agent, _):
        await agent.run(user_prompt="second question", message_history=prior)

    # Prior turn's messages plus the new user prompt.
    assert seen[0] > len(prior)


# ---------------------------------------------------------------------------
# Usage limits
# ---------------------------------------------------------------------------


async def test_turn_stops_at_the_project_request_limit():
    project = make_project(tools=["*"], usage_limits={"request_limit": 3})

    with agent_under_test(
        project, make_user(), always_calls("run_bash", command="ls")
    ) as (
        agent,
        mcp_fake,
    ):
        with pytest.raises(UsageLimitExceeded):
            async for _ in agent.run_stream(user_prompt="loop forever"):
                pass

    # The limit caps model requests, so the loop is bounded rather than
    # endless: three requests each fire one tool call, then the fourth is
    # refused before reaching the model.
    assert len(mcp_fake.calls) == 3


async def test_tool_call_limit_is_enforced():
    project = make_project(tools=["*"], usage_limits={"tool_calls_limit": 2})

    with agent_under_test(
        project, make_user(), always_calls("run_bash", command="ls")
    ) as (
        agent,
        mcp_fake,
    ):
        with pytest.raises(UsageLimitExceeded):
            async for _ in agent.run_stream(user_prompt="loop forever"):
                pass

    assert len(mcp_fake.calls) <= 2


async def test_default_project_has_no_usage_limit_and_completes():
    project = make_project(tools=["*"], usage_limits={})
    model = step_model([call("run_bash", command="ls"), say("done")])

    with agent_under_test(project, make_user(), model) as (agent, _):
        result = await agent.run(user_prompt="hi")

    assert result.usage.requests == 2


# ---------------------------------------------------------------------------
# Elicitation / tool-approval plumbing
#
# The fake toolset is not a real MCP client, so these drive the callbacks
# `run_stream` installs, exactly as the MCP session would.
# ---------------------------------------------------------------------------


def _elicit_toolset(agent_ref: dict, params: mcp.types.ElicitRequestParams):
    toolset = FunctionToolset()

    @toolset.tool_plain
    async def ask() -> str:
        """Ask the user for input."""
        callback = agent_ref["agent"]._cur_mcp_elicitation_callback
        assert callback is not None, "elicitation callback was not installed"
        result = await callback(None, params)
        return f"user action: {result.action}"

    return toolset


async def _run_with_elicitation(project, params, *, resolve):
    """Run a turn whose tool elicits, resolving the request as it streams."""
    agent_ref: dict = {}
    agent = ProjectAgent(project, make_user(), uuid.uuid4())
    agent_ref["agent"] = agent
    toolset = _elicit_toolset(agent_ref, params).filtered(
        lambda ctx, tool: agent._tool_allowed(tool.name)
    )
    model = step_model([call("ask"), say("thanks")])

    events = []
    with agent.agent.override(model=model, toolsets=[toolset]):
        async for event in agent.run_stream(user_prompt="hi", enable_elicitation=True):
            events.append(event)
            resolve(agent, event)
    return events


async def test_form_elicitation_reaches_the_stream_and_resolves():
    project = make_project(tools=["*"])
    params = mcp.types.ElicitRequestFormParams(
        message="Which cluster?",
        requestedSchema={
            "type": "object",
            "properties": {"cluster": {"type": "string"}},
        },
    )

    def resolve(agent, event):
        if isinstance(event, McpFormElicitationEvent):
            agent.resolve_elicitation(
                event.elicitation_id, "accept", {"cluster": "odo"}
            )

    events = await _run_with_elicitation(project, params, resolve=resolve)

    elicitations = [e for e in events if isinstance(e, McpFormElicitationEvent)]
    assert len(elicitations) == 1
    assert elicitations[0].event_kind == "mcp_form_elicitation"
    assert elicitations[0].message == "Which cluster?"
    assert elicitations[0].requested_schema["properties"]["cluster"]["type"] == "string"
    assert any(isinstance(e, ProjectAgentResultEvent) for e in events)


async def test_url_elicitation_reaches_the_stream():
    project = make_project(tools=["*"])
    params = mcp.types.ElicitRequestURLParams(
        message="Log in to Globus",
        url="https://auth.globus.org/authorize",
        elicitationId="globus-1",
    )

    def resolve(agent, event):
        if isinstance(event, McpUrlElicitationEvent):
            agent.resolve_elicitation(event.elicitation_id, "accept")

    events = await _run_with_elicitation(project, params, resolve=resolve)

    elicitations = [e for e in events if isinstance(e, McpUrlElicitationEvent)]
    assert len(elicitations) == 1
    assert elicitations[0].event_kind == "mcp_url_elicitation"
    assert elicitations[0].elicitation_id == "globus-1"
    assert elicitations[0].url == "https://auth.globus.org/authorize"


async def test_declined_elicitation_is_reported_back_to_the_tool():
    project = make_project(tools=["*"])
    params = mcp.types.ElicitRequestFormParams(
        message="Proceed?", requestedSchema={"type": "object", "properties": {}}
    )

    def resolve(agent, event):
        if isinstance(event, McpFormElicitationEvent):
            agent.resolve_elicitation(event.elicitation_id, "decline")

    events = await _run_with_elicitation(project, params, resolve=resolve)

    returned = [
        e.part.content
        for e in events
        if isinstance(e, FunctionToolResultEvent) and hasattr(e.part, "content")
    ]
    assert any("decline" in str(c) for c in returned)


async def test_elicitation_is_not_offered_when_disabled():
    """With elicitation off the callback is absent, so the MCP side gets a cancel."""
    project = make_project(tools=["*"])
    agent = ProjectAgent(project, make_user(), uuid.uuid4())
    toolset = FunctionToolset()

    seen: list[str] = []

    @toolset.tool_plain
    async def ask() -> str:
        """Ask the user for input."""
        seen.append("called")
        assert agent._cur_mcp_elicitation_callback is None
        return "no elicitation channel"

    model = step_model([call("ask"), say("done")])
    with agent.agent.override(model=model, toolsets=[toolset]):
        await agent.run(user_prompt="hi")

    assert seen == ["called"]


async def test_tool_approval_event_is_emitted_and_approved():
    """`_request_tool_approval` is the R6 hook; here it is driven directly."""
    project = make_project(tools=["*"])
    agent = ProjectAgent(project, make_user(), uuid.uuid4())
    toolset = FunctionToolset()

    @toolset.tool_plain
    async def risky() -> str:
        """A tool that needs approval."""
        outcome = await agent._request_tool_approval(
            tool_name="risky", tool_call_id="call-1", args={"force": True}
        )
        return f"approved={outcome.approved}"

    model = step_model([call("risky"), say("done")])
    events = []
    with agent.agent.override(model=model, toolsets=[toolset]):
        async for event in agent.run_stream(user_prompt="hi", enable_elicitation=True):
            events.append(event)
            if isinstance(event, McpToolApprovalEvent):
                agent.resolve_elicitation(event.elicitation_id, "accept")

    approvals = [e for e in events if isinstance(e, McpToolApprovalEvent)]
    assert len(approvals) == 1
    assert approvals[0].event_kind == "mcp_tool_approval"
    assert approvals[0].tool_name == "risky"
    assert approvals[0].args == {"force": True}
    assert any(
        "approved=True" in str(getattr(e.part, "content", ""))
        for e in events
        if isinstance(e, FunctionToolResultEvent)
    )


async def test_tool_approval_fails_closed_without_an_approval_channel():
    agent = ProjectAgent(make_project(), make_user(), uuid.uuid4())
    outcome = await agent._request_tool_approval(
        tool_name="risky", tool_call_id="call-1", args={}
    )
    assert outcome.approved is False
    assert "no approval channel" in (outcome.message or "")


async def test_cancel_elicitations_releases_a_waiting_tool():
    project = make_project(tools=["*"])
    agent = ProjectAgent(project, make_user(), uuid.uuid4())
    params = mcp.types.ElicitRequestFormParams(
        message="Proceed?", requestedSchema={"type": "object", "properties": {}}
    )
    toolset = FunctionToolset()

    @toolset.tool_plain
    async def ask() -> str:
        """Ask the user for input."""
        result = await agent._cur_mcp_elicitation_callback(None, params)
        return f"user action: {result.action}"

    model = step_model([call("ask"), say("done")])
    events = []
    with agent.agent.override(model=model, toolsets=[toolset]):
        async for event in agent.run_stream(user_prompt="hi", enable_elicitation=True):
            events.append(event)
            if isinstance(event, McpFormElicitationEvent):
                # Simulate pool teardown while the tool is still waiting.
                await asyncio.sleep(0)
                agent.cancel_elicitations()

    returned = [
        str(getattr(e.part, "content", ""))
        for e in events
        if isinstance(e, FunctionToolResultEvent)
    ]
    assert any("cancel" in c for c in returned)


async def test_resolving_an_unknown_elicitation_raises():
    agent = ProjectAgent(make_project(), make_user(), uuid.uuid4())
    with pytest.raises(KeyError):
        agent.resolve_elicitation("nope", "accept")
