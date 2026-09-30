"""
The chat-run API: watching, stopping and listing runs, the chat-session fields
that carry run state, and the app lifespan's startup sweep and shutdown stop.

`httpx.ASGITransport` returns a response only once the app has finished it, so
a turn that hangs is started as a background request and ended with Stop.
"""

import asyncio
import json
import uuid
from contextlib import contextmanager

import pytest
from pydantic_ai.toolsets.function import FunctionToolset
from sqlmodel.ext.asyncio.session import AsyncSession

from harness import (
    api_client,
    call,
    make_user,
    parse_sse,
    say,
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
SESSION = "/projects/{name}/chat-session"


def _headers(user) -> dict[str, str]:
    return {"X-Vista-User-Email": user.email}


async def _conversation(session, project, user) -> uuid.UUID:
    row = await chat_session_service.create_chat_session(
        session, project_id=project.id, user_id=user.id
    )
    row_id = row.id
    await session.commit()
    return row_id


@contextmanager
def scripted_agent(project, steps):
    """A real agent on a scripted model whose `hang` tool never returns."""
    hung = asyncio.Event()
    agent = ProjectAgent(project, make_user(), uuid.uuid4())
    toolset = FunctionToolset()

    @toolset.tool_plain
    async def quick() -> str:
        """Finishes at once."""
        return "job 42 submitted"

    @toolset.tool_plain
    async def hang() -> str:
        """Never finishes."""
        hung.set()
        await asyncio.Event().wait()
        return "unreachable"

    filtered = toolset.filtered(lambda ctx, tool: agent._tool_allowed(tool.name))
    with agent.agent.override(model=step_model(steps), toolsets=[filtered]):
        yield agent, hung


async def _hung_turn(client, project, user, conversation_id, hung):
    """Start a turn that blocks in `hang`; returns its still-running request."""
    request = asyncio.create_task(
        client.post(
            RUN.format(name=project.name),
            json={"user_prompt": "submit", "chat_session_id": str(conversation_id)},
            headers=_headers(user),
        )
    )
    async with asyncio.timeout(10):
        await hung.wait()
    return request


async def test_a_send_while_busy_is_a_409_and_changes_nothing(session):
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    conversation = await _conversation(session, project, alice)

    with scripted_agent(project, [call("hang"), say("unreachable")]) as (agent, hung):
        with api_client(session, agent=agent) as (client, _):
            first = await _hung_turn(client, project, alice, conversation, hung)

            busy = await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "again", "chat_session_id": str(conversation)},
                headers=_headers(alice),
            )
            assert busy.status_code == 409
            assert "already has a turn running" in busy.json()["detail"]
            assert chat_run.chat_run_registry.get(conversation) is not None

            stopped = await client.post(
                STOP.format(name=project.name, id=conversation), headers=_headers(alice)
            )
            assert stopped.status_code == 202
            await first


async def test_stop_is_404_when_nothing_is_running(session):
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    conversation = await _conversation(session, project, alice)

    with api_client(session) as (client, _):
        response = await client.post(
            STOP.format(name=project.name, id=conversation), headers=_headers(alice)
        )

    assert response.status_code == 404


async def test_events_are_204_when_idle_and_404_for_a_stranger(session):
    alice = await seed_user(session)
    bob = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    conversation = await _conversation(session, project, alice)

    with api_client(session) as (client, _):
        idle = await client.get(
            EVENTS.format(name=project.name, id=conversation), headers=_headers(alice)
        )
        stranger = await client.get(
            EVENTS.format(name=project.name, id=conversation), headers=_headers(bob)
        )
        unknown = await client.get(
            EVENTS.format(name=project.name, id=uuid.uuid4()), headers=_headers(alice)
        )

    assert idle.status_code == 204
    assert stranger.status_code == 403
    assert unknown.status_code == 404


async def test_events_replay_from_the_start_and_resume_after_a_seq(session):
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    conversation = await _conversation(session, project, alice)

    with scripted_agent(project, [call("hang"), say("unreachable")]) as (agent, hung):
        with api_client(session, agent=agent) as (client, _):
            turn = await _hung_turn(client, project, alice, conversation, hung)
            url = EVENTS.format(name=project.name, id=conversation)
            watch_all = asyncio.create_task(client.get(url, headers=_headers(alice)))
            watch_tail = asyncio.create_task(
                client.get(url, params={"after": 3}, headers=_headers(alice))
            )
            await asyncio.sleep(0.05)
            await client.post(
                STOP.format(name=project.name, id=conversation), headers=_headers(alice)
            )
            everything, tail = await watch_all, await watch_tail
            await turn

    events = parse_sse(everything.text)
    assert events[0][0] == "run_started"
    assert events[-1][0] == "run_finished"
    assert json.loads(events[-1][1])["state"] == "stopped"
    ids = [
        int(line.removeprefix("id:"))
        for line in everything.text.splitlines()
        if line.startswith("id:")
    ]
    assert ids == list(range(1, len(events) + 1))
    assert [name for name, _ in parse_sse(tail.text)] == [
        name for name, _ in events[3:]
    ]


async def test_status_lists_working_then_done_unseen_then_nothing_once_acked(session):
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    hung_conversation = await _conversation(session, project, alice)
    quiet = await _conversation(session, project, alice)

    with scripted_agent(project, [call("hang"), say("unreachable")]) as (agent, hung):
        with api_client(session, agent=agent) as (client, _):
            turn = await _hung_turn(client, project, alice, hung_conversation, hung)
            working = await client.get(
                STATUS.format(name=project.name), headers=_headers(alice)
            )
            await client.post(
                STOP.format(name=project.name, id=hung_conversation),
                headers=_headers(alice),
            )
            await turn
            after_stop = await client.get(
                STATUS.format(name=project.name), headers=_headers(alice)
            )

    assert working.json() == [
        {
            "chat_session_id": str(hung_conversation),
            "status": "working",
            "unseen": False,
        }
    ]
    assert str(quiet) not in working.text, "idle conversations are omitted"
    assert after_stop.json() == [], "a stop is the researcher's own action"

    with scripted_agent(project, [say("finished")]) as (agent, _hung):
        with api_client(session, agent=agent) as (client, _):
            await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "hi", "chat_session_id": str(quiet)},
                headers=_headers(alice),
            )
            done = await client.get(
                STATUS.format(name=project.name), headers=_headers(alice)
            )
            await client.put(
                SESSION.format(name=project.name),
                params={"chat_session_id": str(quiet)},
                json={"ack_run": True},
                headers=_headers(alice),
            )
            acked = await client.get(
                STATUS.format(name=project.name), headers=_headers(alice)
            )

    assert done.json() == [
        {"chat_session_id": str(quiet), "status": "done", "unseen": True}
    ]
    assert acked.json() == []


async def test_the_chat_session_reports_run_state_and_ack_clears_the_events(session):
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    conversation = await _conversation(session, project, alice)

    with scripted_agent(project, [say("finished")]) as (agent, _hung):
        with api_client(session, agent=agent) as (client, _):
            await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "hi", "chat_session_id": str(conversation)},
                headers=_headers(alice),
            )
            params = {"chat_session_id": str(conversation)}
            before = (
                await client.get(
                    SESSION.format(name=project.name),
                    params=params,
                    headers=_headers(alice),
                )
            ).json()
            after = (
                await client.put(
                    SESSION.format(name=project.name),
                    params=params,
                    json={"ack_run": True, "message_history": []},
                    headers=_headers(alice),
                )
            ).json()

    assert before["run_status"] == "done" and before["run_unseen"] is True
    assert before["run_events"][0]["event"] == "run_started"
    assert before["message_history"], "the backend saved the turn"
    assert (after["run_status"], after["run_unseen"], after["run_events"]) == (
        "idle",
        False,
        None,
    )
    assert after["message_history"], "a PUT cannot erase model history"


async def test_a_running_conversation_reports_working(session):
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    conversation = await _conversation(session, project, alice)

    with scripted_agent(project, [call("hang"), say("unreachable")]) as (agent, hung):
        with api_client(session, agent=agent) as (client, _):
            turn = await _hung_turn(client, project, alice, conversation, hung)
            live = (
                await client.get(
                    SESSION.format(name=project.name),
                    params={"chat_session_id": str(conversation)},
                    headers=_headers(alice),
                )
            ).json()
            await client.post(
                STOP.format(name=project.name, id=conversation), headers=_headers(alice)
            )
            await turn

    assert live["run_status"] == "working"


async def test_deleting_a_conversation_ends_its_run(session):
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    conversation = await _conversation(session, project, alice)

    with scripted_agent(project, [call("hang"), say("unreachable")]) as (agent, hung):
        with api_client(session, agent=agent) as (client, _):
            turn = await _hung_turn(client, project, alice, conversation, hung)
            deleted = await client.delete(
                SESSION.format(name=project.name),
                params={"chat_session_id": str(conversation)},
                headers=_headers(alice),
            )
            await turn

    assert deleted.status_code == 204
    assert chat_run.chat_run_registry.get(conversation) is None


# ---------------------------------------------------------------------------
# Lifespan: startup sweep and shutdown stop
# ---------------------------------------------------------------------------


class _FakeMcpServer:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def list_tools(self):
        return []


@pytest.fixture
def lifespan_env(engine, monkeypatch):
    async def no_init() -> None:
        return None

    monkeypatch.setattr(api_module, "init_db", no_init)
    monkeypatch.setattr(api_module, "get_vista_mcp_server", lambda: _FakeMcpServer())
    monkeypatch.setattr(chat_run, "session_factory", lambda: AsyncSession(engine))
    monkeypatch.setattr(api_module.settings.campaigns, "monitor_enabled", False)


async def test_startup_marks_a_running_row_interrupted_and_unseen(
    session, engine, lifespan_env
):
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    conversation = await _conversation(session, project, alice)
    async with AsyncSession(engine) as other:
        row = await other.get(ChatSessionTable, conversation)
        assert row is not None
        row.run_state = "running"
        await other.commit()

    async with api_module.lifespan(api_module.app):
        pass

    async with AsyncSession(engine) as other:
        row = await other.get(ChatSessionTable, conversation)
        assert row is not None
        assert (row.run_state, row.run_unseen) == ("interrupted", True)


async def test_shutdown_interrupts_a_running_turn_and_saves_its_history(
    session, engine, lifespan_env
):
    alice = await seed_user(session)
    project = await seed_project(session, alice, tools=["*"])
    conversation = await _conversation(session, project, alice)

    with scripted_agent(project, [call("quick"), call("hang"), say("x")]) as (
        agent,
        hung,
    ):
        with api_client(session, agent=agent) as (client, _):
            async with api_module.lifespan(api_module.app):
                turn = await _hung_turn(client, project, alice, conversation, hung)
            await turn  # the lifespan exit stopped it

    async with AsyncSession(engine) as other:
        row = await other.get(ChatSessionTable, conversation)
        assert row is not None
        assert (row.run_state, row.run_unseen) == ("interrupted", True)
        assert "job 42 submitted" in json.dumps(row.message_history)
        assert "Interrupted when VISTA quit." in json.dumps(row.message_history)
