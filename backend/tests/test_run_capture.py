"""
`ProjectAgent.run_stream(capture=...)` keeps a cancelled turn's completed steps.

Design D3 of background-chat-runs rests on `capture_run_messages()` still seeing
messages when the run's task is cancelled mid-tool. If this stops holding, the
design's fallback is `agent.iter()`.
"""

import asyncio
import uuid

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.toolsets.function import FunctionToolset

from harness import agent_under_test, call, make_project, make_user, say, step_model
from vista_backend.agents.agents import (
    ProjectAgent,
    ProjectAgentResultEvent,
    RunCapture,
)
from vista_backend.services.run_history import STOPPED_NOTE, trim_partial_history

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


def _toolset(started: asyncio.Event) -> FunctionToolset:
    toolset = FunctionToolset()

    @toolset.tool_plain
    async def quick() -> str:
        """Finishes at once."""
        return "job 42 submitted"

    @toolset.tool_plain
    async def hang() -> str:
        """Never finishes."""
        started.set()
        await asyncio.Event().wait()
        return "unreachable"

    return toolset


async def test_cancelling_mid_tool_still_exposes_the_completed_tool_call():
    started = asyncio.Event()
    agent = ProjectAgent(make_project(tools=["*"]), make_user(), uuid.uuid4())
    toolset = _toolset(started).filtered(
        lambda ctx, tool: agent._tool_allowed(tool.name)
    )
    model = step_model([call("quick"), call("hang"), say("unreachable")])
    capture = RunCapture()

    async def drive() -> None:
        async for _ in agent.run_stream(user_prompt="submit it", capture=capture):
            pass

    with agent.agent.override(model=model, toolsets=[toolset]):
        task = asyncio.create_task(drive())
        await asyncio.wait_for(started.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    returns = [
        p
        for m in capture.messages
        if hasattr(m, "parts")
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert [r.tool_name for r in returns] == ["quick"]

    history = trim_partial_history(
        [], capture.messages, user_prompt="submit it", note=STOPPED_NOTE
    )
    calls = [
        p.tool_name
        for m in history
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, ToolCallPart)
    ]
    assert calls == ["quick"], "the call in flight is dropped, the finished one kept"
    last = history[-1]
    assert isinstance(last, ModelResponse)
    assert isinstance(last.parts[-1], TextPart)
    assert last.parts[-1].content == STOPPED_NOTE


async def test_a_finished_turn_is_unchanged_by_capturing():
    capture = RunCapture()

    with agent_under_test(
        make_project(tools=["*"]), make_user(), step_model([say("hello")])
    ) as (agent, _):
        events = [e async for e in agent.run_stream(user_prompt="hi", capture=capture)]

    result = next(e for e in events if isinstance(e, ProjectAgentResultEvent)).result
    assert capture.messages[-len(result.new_messages) :] == result.new_messages
