"""
Acceptance tests for the `chat-runs` spec (openspec/changes/background-chat-runs),
driven end to end through the HTTP API on a scripted model.

Each test names the spec scenario it covers. The narrower behaviours (registry,
compaction, status rules) are in `test_chat_run.py` and `test_chat_run_api.py`.

`httpx.ASGITransport` returns a response only when the app has finished it, so a
turn that blocks is started as a background request. Cancelling that request
is how these tests play a client that goes away.
"""

import asyncio
import json
import uuid
from contextlib import contextmanager

import mcp.types
import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.toolsets.function import FunctionToolset
from sqlmodel.ext.asyncio.session import AsyncSession

from harness import (
    api_client,
    call,
    make_user,
    parse_sse,
    say,
    scripted_model,
    seed_project,
    seed_user,
    step_model,
)
from vista_backend.agents.agents import ProjectAgent
from vista_backend.api import api as api_module
from vista_backend.db.schemas import ChatSessionTable
from vista_backend.services import chat_run, chat_session as chat_session_service

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

RUN = "/projects/{name}/agent/run"
EVENTS = "/projects/{name}/chat-sessions/{id}/run/events"
STOP = "/projects/{name}/chat-sessions/{id}/run/stop"
STATUS = "/projects/{name}/chat-runs/status"
ELICITATION = "/projects/{name}/elicitation"


def _headers(user) -> dict[str, str]:
    return {"X-Vista-User-Email": user.email}


class Turn:
    """The scripted agent's handles: where a turn is blocked, and how to free it."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.at_gate = asyncio.Event()
        self.at_hang = asyncio.Event()


@contextmanager
def scripted_agent(project, model):
    """
    A real agent on `model`, with tools a test can steer:
    `quick` returns, `gate` blocks until released, `hang` never returns,
    and `ask` raises a form elicitation, as Lux's SSH login does.
    """
    turn = Turn()
    agent = ProjectAgent(project, make_user(), uuid.uuid4())
    toolset = FunctionToolset()

    @toolset.tool_plain
    async def quick() -> str:
        """Finishes at once."""
        return "job 42 submitted"

    @toolset.tool_plain
    async def gate() -> str:
        """Blocks until the test releases it."""
        turn.at_gate.set()
        await turn.gate.wait()
        return "gate passed"

    @toolset.tool_plain
    async def hang() -> str:
        """Never finishes."""
        turn.at_hang.set()
        await asyncio.Event().wait()
        return "unreachable"

    @toolset.tool_plain
    async def ask() -> str:
        """Ask the researcher for an SSH login."""
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

    filtered = toolset.filtered(lambda ctx, tool: agent._tool_allowed(tool.name))
    with agent.agent.override(model=model, toolsets=[filtered]):
        yield agent, turn


async def _conversation(session, project, user) -> uuid.UUID:
    row = await chat_session_service.create_chat_session(
        session, project_id=project.id, user_id=user.id
    )
    row_id = row.id
    await session.commit()
    return row_id


def _send(client, project, user, conversation, prompt="go", *, stream=True):
    return asyncio.create_task(
        client.post(
            RUN.format(name=project.name),
            json={
                "user_prompt": prompt,
                "stream": stream,
                "chat_session_id": str(conversation),
            },
            headers=_headers(user),
        )
    )


async def _until(condition, timeout: float = 10) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.005)


async def _saved(engine, conversation) -> ChatSessionTable:
    async with AsyncSession(engine) as other:
        row = await other.get(ChatSessionTable, conversation)
        assert row is not None
        return row


async def _cancel(request: asyncio.Task) -> None:
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request


# ---------------------------------------------------------------------------
# A run outlives the page that started it
# ---------------------------------------------------------------------------


async def test_navigating_away_mid_run_completes_the_turn_and_saves_it(session, engine):
    """Scenario: Researcher navigates away mid-run."""
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    conversation = await _conversation(session, project, alice)

    with scripted_agent(project, step_model([call("gate"), say("the answer")])) as (
        agent,
        turn,
    ):
        with api_client(session, agent=agent) as (client, _):
            request = _send(client, project, alice, conversation)
            await _until(turn.at_gate.is_set)

            await _cancel(request)  # the page disconnects
            run = chat_run.chat_run_registry.get(conversation)
            assert run is not None and run.state == "running"

            turn.gate.set()
            await run.wait()

    saved = await _saved(engine, conversation)
    assert saved.run_state == "done"
    assert "the answer" in json.dumps(saved.message_history)


# ---------------------------------------------------------------------------
# Re-attaching to a run
# ---------------------------------------------------------------------------


async def test_reattaching_replays_the_run_from_its_start(session):
    """Scenarios: Returning to a running conversation; Two views of one run."""
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    conversation = await _conversation(session, project, alice)

    with scripted_agent(project, step_model([call("gate"), say("the answer")])) as (
        agent,
        turn,
    ):
        with api_client(session, agent=agent) as (client, _):
            first = _send(client, project, alice, conversation)
            await _until(turn.at_gate.is_set)
            await _cancel(first)

            url = EVENTS.format(name=project.name, id=conversation)
            window = asyncio.create_task(client.get(url, headers=_headers(alice)))
            browser_tab = asyncio.create_task(client.get(url, headers=_headers(alice)))
            await asyncio.sleep(0.05)
            turn.gate.set()
            window_events, tab_events = await window, await browser_tab

    for response in (window_events, tab_events):
        events = parse_sse(response.text)
        kinds = [name for name, _ in events]
        assert kinds[0] == "run_started", "replayed from the very start"
        assert "function_tool_call" in kinds, "the steps taken so far"
        assert kinds[-1] == "run_finished"
        assert json.loads(events[-1][1])["state"] == "done"
    assert window_events.text == tab_events.text, "each view receives every event"


# ---------------------------------------------------------------------------
# One active run per conversation
# ---------------------------------------------------------------------------


async def test_a_send_while_busy_is_refused_and_the_running_turn_finishes(
    session, engine
):
    """Scenario: Send while busy."""
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    conversation = await _conversation(session, project, alice)

    with scripted_agent(project, step_model([call("gate"), say("first answer")])) as (
        agent,
        turn,
    ):
        with api_client(session, agent=agent) as (client, _):
            running = _send(client, project, alice, conversation, "first")
            await _until(turn.at_gate.is_set)

            refused = await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "second", "chat_session_id": str(conversation)},
                headers=_headers(alice),
            )
            turn.gate.set()
            finished = await running

    assert refused.status_code == 409
    assert finished.status_code == 200
    blob = json.dumps((await _saved(engine, conversation)).message_history)
    assert "first answer" in blob and "second" not in blob


# ---------------------------------------------------------------------------
# Stop keeps what happened
# ---------------------------------------------------------------------------


async def test_the_next_turn_knows_about_a_job_submitted_before_a_stop(session, engine):
    """Scenario: Stop after a job was submitted."""
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    conversation = await _conversation(session, project, alice)

    with scripted_agent(
        project, step_model([call("quick"), call("hang"), say("unreachable")])
    ) as (agent, turn):
        with api_client(session, agent=agent) as (client, _):
            running = _send(client, project, alice, conversation, "submit it")
            await _until(turn.at_hang.is_set)
            stopped = await client.post(
                STOP.format(name=project.name, id=conversation), headers=_headers(alice)
            )
            response = await running
    assert stopped.status_code == 202
    states = [
        json.loads(data)["state"]
        for name, data in parse_sse(response.text)
        if name == "run_finished"
    ]
    assert states == ["stopped"]
    saved = await _saved(engine, conversation)
    assert saved.run_state == "stopped"

    seen: list[list[ModelMessage]] = []

    def driver(messages, info):
        seen.append(list(messages))
        return ModelResponse(parts=[TextPart("I remember job 42")])

    with scripted_agent(project, scripted_model(driver)) as (agent, _turn):
        with api_client(session, agent=agent) as (client, _):
            follow_up = await client.post(
                RUN.format(name=project.name),
                json={
                    "user_prompt": "what did you submit?",
                    "chat_session_id": str(conversation),
                },
                headers=_headers(alice),
            )

    assert follow_up.status_code == 200
    history = json.dumps(
        [m.__dict__ if hasattr(m, "__dict__") else m for m in seen[0]], default=str
    )
    assert "job 42 submitted" in history, "the completed tool call and its result"
    assert "Stopped by the researcher." in history
    assert "unreachable" not in history


# ---------------------------------------------------------------------------
# Prompts wait for the researcher
# ---------------------------------------------------------------------------


async def test_an_answered_prompt_is_not_shown_again_on_reattach(session):
    """Scenarios: Answered prompt is not replayed; Answer in one view clears the other."""
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    conversation = await _conversation(session, project, alice)

    with scripted_agent(
        project, step_model([call("ask"), call("gate"), say("ok")])
    ) as (
        agent,
        turn,
    ):
        with api_client(session, agent=agent) as (client, _):
            first = _send(client, project, alice, conversation)
            run = None
            async with asyncio.timeout(10):
                while run is None or not run.pending:
                    run = chat_run.chat_run_registry.get(conversation)
                    await asyncio.sleep(0.005)
            prompt_id = next(iter(run.pending))
            url = EVENTS.format(name=project.name, id=conversation)

            # A second view is open while the prompt is still unanswered.
            other_view = asyncio.create_task(client.get(url, headers=_headers(alice)))
            await asyncio.sleep(0.05)

            answered = await client.post(
                ELICITATION.format(name=project.name),
                json={
                    "id": prompt_id,
                    "action": "accept",
                    "content": {"password": "hunter2"},
                },
                headers=_headers(alice),
            )
            assert answered.status_code == 200
            await _until(turn.at_gate.is_set)

            reattached = asyncio.create_task(client.get(url, headers=_headers(alice)))
            await asyncio.sleep(0.05)
            turn.gate.set()
            await first
            other, late = await other_view, await reattached

    def prompt_events(response, kind):
        return [
            json.loads(data) for name, data in parse_sse(response.text) if name == kind
        ]

    assert len(prompt_events(other, "mcp_form_elicitation")) == 1
    assert [e["elicitation_id"] for e in prompt_events(other, "prompt_resolved")] == [
        prompt_id
    ], "the view that showed it is told to clear it"
    assert prompt_events(late, "mcp_form_elicitation") == [], "already answered"
    assert prompt_events(late, "prompt_resolved") == []
    assert "hunter2" not in other.text + late.text, "answers never enter the events"


# ---------------------------------------------------------------------------
# Unseen outcomes survive a restart
# ---------------------------------------------------------------------------


class _FakeMcpServer:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def list_tools(self):
        return []


async def test_unseen_outcomes_survive_an_app_restart(session, engine, monkeypatch):
    """Scenario: Unseen survives restart (and: Quitting VISTA ends runs)."""

    async def no_init() -> None:
        return None

    monkeypatch.setattr(api_module, "init_db", no_init)
    monkeypatch.setattr(api_module, "get_vista_mcp_server", lambda: _FakeMcpServer())
    monkeypatch.setattr(api_module.settings.campaigns, "monitor_enabled", False)
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    finished = await _conversation(session, project, alice)
    killed = await _conversation(session, project, alice)

    with scripted_agent(project, step_model([say("done while away")])) as (agent, _):
        with api_client(session, agent=agent) as (client, _):
            await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "hi", "chat_session_id": str(finished)},
                headers=_headers(alice),
            )
            # A second conversation whose process died mid-turn (no grace period).
            async with AsyncSession(engine) as other:
                row = await other.get(ChatSessionTable, killed)
                assert row is not None
                row.run_state = "running"
                await other.commit()

            async with api_module.lifespan(api_module.app):  # the next launch
                pass
            status = await client.get(
                STATUS.format(name=project.name), headers=_headers(alice)
            )

    assert sorted((s["status"], s["unseen"]) for s in status.json()) == [
        ("done", True),
        ("interrupted", True),
    ]
    by_id = {s["chat_session_id"]: s["status"] for s in status.json()}
    assert by_id == {str(finished): "done", str(killed): "interrupted"}
