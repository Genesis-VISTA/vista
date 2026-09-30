"""
The chat run registry (`services/chat_run.py`): a turn as a background task that
outlives its watchers, saves its own outcome, and can be stopped.

Turns run on a scripted model with a fake toolset; the run's database writes go
to a real SQLite file through the registry's session factory.
"""

import asyncio
import json
import uuid
from contextlib import ExitStack, asynccontextmanager

import mcp.types
import pytest
from pydantic_ai.messages import (
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.toolsets.function import FunctionToolset
from sqlmodel.ext.asyncio.session import AsyncSession

from harness import call, make_project, make_user, say, seed_project, seed_user
from harness import step_model
from vista_backend.agents.agents import ProjectAgent
from vista_backend.db.schemas import ChatSessionTable
from vista_backend.services import chat_run, chat_session as chat_session_service
from vista_backend.services.chat_run import (
    ChatRunRegistry,
    resolve_prompt,
    RunBusy,
    RunEvent,
    compact_events,
)
from vista_backend.services.run_history import STOPPED_NOTE

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def _until(condition, timeout: float = 10) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.005)


class Env:
    """A project, a researcher, and scripted agents keyed by conversation."""

    def __init__(self, engine, session, stack: ExitStack) -> None:
        self.engine = engine
        self.session = session
        self.stack = stack
        self.agents: dict[uuid.UUID, ProjectAgent] = {}
        self.hang_started = asyncio.Event()
        self.registry = ChatRunRegistry()

    async def conversation(self, steps, history=None) -> tuple[ChatSessionTable, tuple]:
        row = await chat_session_service.create_chat_session(
            self.session, project_id=self.project.id, user_id=self.user.id
        )
        if history:
            row.message_history = chat_session_service.dump_message_history(history)
        await self.session.commit()
        await self.session.refresh(row)
        self.session.expunge(row)  # later commits must not expire it
        agent = ProjectAgent(make_project(tools=["*"]), make_user(), row.id)
        toolset = self._toolset(agent).filtered(
            lambda ctx, tool: agent._tool_allowed(tool.name)
        )
        self.stack.enter_context(
            agent.agent.override(model=step_model(steps), toolsets=[toolset])
        )
        self.agents[row.id] = agent
        return row, (row.id, self.project.id, self.user.id)

    def _toolset(self, agent: ProjectAgent) -> FunctionToolset:
        toolset = FunctionToolset()
        hang_started = self.hang_started

        @toolset.tool_plain
        async def ask() -> str:
            """Ask the researcher a question, as Lux's SSH login does."""
            callback = agent._cur_mcp_elicitation_callback
            assert callback is not None
            result = await callback(
                None,
                mcp.types.ElicitRequestFormParams(
                    message="SSH login",
                    requestedSchema={"type": "object", "properties": {}},
                ),
            )
            return f"action: {result.action}"

        @toolset.tool_plain
        async def quick() -> str:
            """Finishes at once."""
            return "job 42 submitted"

        @toolset.tool_plain
        async def hang() -> str:
            """Never finishes."""
            hang_started.set()
            await asyncio.Event().wait()
            return "unreachable"

        return toolset

    async def start(self, row, key, prompt="go"):
        return await self.registry.start(
            chat_session_id=row.id,
            project_id=self.project.id,
            user_id=self.user.id,
            agent_key=key,
            user_prompt=prompt,
            prior_history=chat_session_service._MESSAGE_HISTORY_ADAPTER.validate_python(
                row.message_history
            ),
        )

    async def row(self, row_id) -> ChatSessionTable:
        async with AsyncSession(self.engine) as s:
            found = await s.get(ChatSessionTable, row_id)
            assert found is not None
            return found


@pytest.fixture
async def env(engine, session, monkeypatch):
    @asynccontextmanager
    async def get(key):
        yield holder.agents[key[0]]

    class Pool:
        pass

    Pool.get = staticmethod(get)  # type: ignore[attr-defined]
    monkeypatch.setattr(chat_run, "session_factory", lambda: AsyncSession(engine))
    monkeypatch.setattr(chat_run, "project_agent_pool", Pool)
    with ExitStack() as stack:
        holder = Env(engine, session, stack)
        monkeypatch.setattr(chat_run, "chat_run_registry", holder.registry)
        user = await seed_user(session)
        holder.user = user
        holder.project = await seed_project(session, user)
        await session.commit()
        yield holder


def _texts(history) -> list[str]:
    return [
        p.content
        for m in history
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, TextPart)
    ]


async def test_a_run_can_be_started_subscribed_to_and_awaited(env):
    row, key = await env.conversation([say("hello")])

    run = await env.start(row, key)
    seen = [event async for event in run.subscribe()]
    await run.wait()

    assert seen[0].kind == "run_started"
    assert json.loads(seen[0].data) == {
        "event_kind": "run_started",
        "run_id": run.run_id,
        "user_prompt": "go",
    }
    assert seen[-1].kind == "run_finished"
    assert json.loads(seen[-1].data) == {"event_kind": "run_finished", "state": "done"}
    assert [e.seq for e in seen] == list(range(1, len(seen) + 1))
    assert env.registry.get(row.id) is None, "a finished run leaves the registry"


async def test_a_finished_run_saves_its_outcome_without_anyone_watching(env):
    prior = [ModelResponse(parts=[TextPart(content="earlier answer")])]
    row, key = await env.conversation([say("hello there")], history=prior)

    run = await env.start(row, key)
    await run.wait()  # nobody subscribed

    saved = await env.row(row.id)
    assert saved.run_state == "done"
    assert saved.run_unseen is True
    history = chat_session_service._MESSAGE_HISTORY_ADAPTER.validate_python(
        saved.message_history
    )
    assert _texts(history) == ["earlier answer", "hello there"]


async def test_saved_events_are_compacted_and_hold_no_prompts_or_deltas(env):
    row, key = await env.conversation([say("hello there")])

    run = await env.start(row, key)
    await run.wait()

    kinds = [e["event"] for e in (await env.row(row.id)).run_events or []]
    assert kinds[0] == "run_started" and kinds[-1] == "run_finished"
    assert "part_delta" not in kinds
    assert not {"mcp_form_elicitation", "mcp_url_elicitation", "mcp_tool_approval"} & (
        set(kinds)
    )
    assert "part_start" in kinds and "agent_run_result" in kinds


def test_compaction_folds_a_parts_deltas_into_its_start():
    def event(seq, kind, data):
        return RunEvent(seq=seq, kind=kind, data=json.dumps(data))

    part = lambda text: {"part_kind": "text", "content": text}  # noqa: E731
    events = [
        event(1, "run_started", {"run_id": "r", "user_prompt": "q"}),
        event(2, "part_start", {"index": 0, "part": part("He")}),
        event(3, "part_delta", {"index": 0, "delta": {"content_delta": "llo"}}),
        event(4, "part_end", {"index": 0, "part": part("Hello")}),
        event(5, "mcp_tool_approval", {"elicitation_id": "e"}),
        event(6, "prompt_resolved", {"elicitation_id": "e"}),
        event(7, "run_finished", {"state": "done"}),
    ]

    out = compact_events(events)

    assert [e["event"] for e in out] == [
        "run_started",
        "part_start",
        "part_end",
        "run_finished",
    ]
    assert out[1]["data"]["part"]["content"] == "Hello"


async def test_a_late_subscriber_replays_from_the_start_and_after_skips(env):
    row, key = await env.conversation([say("hello")])
    run = await env.start(row, key)
    await run.wait()

    everything = [e async for e in run.subscribe()]
    tail = [e async for e in run.subscribe(after=2)]

    assert len(everything) > 3
    assert [e.seq for e in tail] == [e.seq for e in everything[2:]]


async def test_two_subscribers_each_receive_every_event(env):
    row, key = await env.conversation([say("hello")])
    run = await env.start(row, key)

    async def collect():
        return [e.seq async for e in run.subscribe()]

    first, second = await asyncio.gather(collect(), collect())

    assert first == second and len(first) > 2


async def test_closing_a_subscriber_does_not_cancel_the_run(env):
    row, key = await env.conversation([call("quick"), say("all done")])
    run = await env.start(row, key)

    stream = run.subscribe()
    await anext(stream)
    await stream.aclose()  # the page navigated away
    await run.wait()

    assert (await env.row(row.id)).run_state == "done"


async def test_stop_keeps_the_tool_call_that_completed(env):
    row, key = await env.conversation([call("quick"), call("hang"), say("unreachable")])
    run = await env.start(row, key, prompt="submit it")

    await _until(env.hang_started.is_set)
    assert await env.registry.stop(row.id) is True

    saved = await env.row(row.id)
    assert saved.run_state == "stopped"
    assert saved.run_unseen is False, "a stop is the researcher's own action"
    history = chat_session_service._MESSAGE_HISTORY_ADAPTER.validate_python(
        saved.message_history
    )
    calls = [
        p.tool_name
        for m in history
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, ToolCallPart)
    ]
    returns = [
        p.content
        for m in history
        if hasattr(m, "parts")
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert calls == ["quick"]
    assert returns == ["job 42 submitted"]
    assert _texts(history)[-1] == STOPPED_NOTE
    assert json.loads(run.events[-1].data)["state"] == "stopped"
    assert env.registry.get(row.id) is None


async def test_stop_reports_false_when_nothing_is_running(env):
    assert await env.registry.stop(uuid.uuid4()) is False


async def test_a_second_start_in_a_busy_conversation_is_refused(env):
    row, key = await env.conversation([call("hang"), say("unreachable")])
    run = await env.start(row, key)
    await _until(env.hang_started.is_set)

    with pytest.raises(RunBusy):
        await env.start(row, key, prompt="again")

    assert run.state == "running", "the running turn is unaffected"
    await env.registry.stop(row.id)


async def test_different_conversations_run_at_the_same_time(env):
    row_a, key_a = await env.conversation([call("hang"), say("unreachable")])
    row_b, key_b = await env.conversation([say("b's answer")])

    run_a = await env.start(row_a, key_a)
    await _until(env.hang_started.is_set)
    run_b = await env.start(row_b, key_b)
    await run_b.wait()

    assert run_a.state == "running"
    saved_b = await env.row(row_b.id)
    assert saved_b.run_state == "done"
    assert (await env.row(row_a.id)).run_state == "running"
    await env.registry.stop(row_a.id)
    assert _texts(
        chat_session_service._MESSAGE_HISTORY_ADAPTER.validate_python(
            (await env.row(row_b.id)).message_history
        )
    ) == ["b's answer"]


async def test_stop_all_interrupts_every_run(env):
    row, key = await env.conversation([call("hang"), say("unreachable")])
    await env.start(row, key)
    await _until(env.hang_started.is_set)

    await env.registry.stop_all(reason="interrupted")

    saved = await env.row(row.id)
    assert saved.run_state == "interrupted"
    assert saved.run_unseen is True


# ---------------------------------------------------------------------------
# Prompts wait for the researcher
# ---------------------------------------------------------------------------

PROMPTS = {"mcp_form_elicitation", "mcp_url_elicitation", "mcp_tool_approval"}


async def test_a_prompt_does_not_time_out_and_stop_ends_it(env, monkeypatch):
    async def no_timeouts(*args, **kwargs):
        raise AssertionError("a prompt must not be waited on with a timeout")

    row, key = await env.conversation([call("ask"), say("unreachable")])
    run = await env.start(row, key)
    await _until(lambda: bool(run.pending))
    monkeypatch.setattr(asyncio, "wait_for", no_timeouts)

    await asyncio.sleep(0.05)  # long enough for any timeout written as a wait_for
    assert run.state == "running" and run.pending, "still waiting for the researcher"

    assert await env.registry.stop(row.id) is True
    assert (await env.row(row.id)).run_state == "stopped"
    assert await resolve_prompt(next(iter(run.pending)), "accept") is False, (
        "the prompt was withdrawn"
    )


async def test_replay_shows_only_unanswered_prompts_and_a_live_view_sees_resolution(
    env,
):
    row, key = await env.conversation([call("ask"), call("ask"), say("done")])
    run = await env.start(row, key)

    live: list = []

    async def watch():
        async for event in run.subscribe():
            live.append(event)

    watcher = asyncio.create_task(watch())
    await _until(lambda: len(run.pending) == 1)
    first = next(iter(run.pending))
    assert await resolve_prompt(first, "accept", {"password": "hunter2"}) is True
    await _until(lambda: len(run.pending) == 1 and first not in run.pending)
    second = next(iter(run.pending))

    late = []
    late_watcher = asyncio.create_task(_collect(run, late))
    await _until(lambda: any(e.prompt_id == second for e in late))
    assert await resolve_prompt(second, "decline") is True
    await run.wait()
    await asyncio.gather(watcher, late_watcher)

    def seen(events):
        return [(e.kind, e.prompt_id) for e in events if e.prompt_id is not None]

    assert ("prompt_resolved", first) in seen(live), "a live view clears the prompt"
    assert ("prompt_resolved", second) in seen(live)
    assert [k for k, i in seen(late) if i == first] == [], "answered before it joined"
    assert ("mcp_form_elicitation", second) in seen(late)
    assert ("prompt_resolved", second) in seen(late), "it saw this one live"
    assert "hunter2" not in "".join(e.data for e in run.events), (
        "answers are not stored"
    )
    assert "hunter2" not in json.dumps((await env.row(row.id)).run_events)


async def _collect(run, into: list) -> None:
    async for event in run.subscribe():
        into.append(event)


async def test_a_second_answer_is_refused(env):
    row, key = await env.conversation([call("ask"), say("done")])
    run = await env.start(row, key)
    await _until(lambda: bool(run.pending))
    prompt = next(iter(run.pending))

    assert await resolve_prompt(prompt, "accept") is True
    assert await resolve_prompt(prompt, "accept") is False
    await run.wait()


def test_the_vista_mcp_server_waits_up_to_a_day_for_a_tool_call():
    from vista_backend.agents.agents import get_dev_mcp_server, get_vista_mcp_server

    assert get_vista_mcp_server().read_timeout == 24 * 60 * 60
    assert get_dev_mcp_server([]).read_timeout == 1800 + 60, "sandbox is unchanged"
