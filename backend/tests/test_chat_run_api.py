"""
The chat-run API: watching, stopping and listing runs, the chat-session fields
that carry run state, and the app lifespan's startup sweep and shutdown stop.

`httpx.ASGITransport` returns a response only once the app has finished it, so
a turn that hangs is started as a background request and ended with Stop.
"""

import asyncio
import json
import uuid
import os
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from pydantic_ai.toolsets.function import FunctionToolset
from sqlmodel.ext.asyncio.session import AsyncSession

from harness import (
    FakeAgentPool,
    api_client,
    call,
    make_local_settings,
    parse_sse,
    say,
    seed_project,
    step_model,
)
from vista_backend.agents.agents import ProjectAgent
from vista_backend.api import api as api_module
from vista_backend.db.schemas import ChatSessionTable, DisplayedFileTable
from vista_backend.services import (
    chat_run,
    chat_session as chat_session_service,
    displayed_files as displayed_files_service,
)

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

RUN = "/projects/{name}/agent/run"
EVENTS = "/projects/{name}/chat-sessions/{id}/run/events"
STOP = "/projects/{name}/chat-sessions/{id}/run/stop"
STATUS = "/projects/{name}/chat-runs/status"
SESSION = "/projects/{name}/chat-session"


async def _conversation(session, project) -> uuid.UUID:
    row = await chat_session_service.create_chat_session(session, project_id=project.id)
    row_id = row.id
    await session.commit()
    return row_id


@contextmanager
def scripted_agent(project, steps):
    """A real agent on a scripted model whose `hang` tool never returns."""
    hung = asyncio.Event()
    agent = ProjectAgent(project, make_local_settings(), uuid.uuid4())
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


async def _hung_turn(client, project, conversation_id, hung):
    """Start a turn that blocks in `hang`; returns its still-running request."""
    request = asyncio.create_task(
        client.post(
            RUN.format(name=project.name),
            json={"user_prompt": "submit", "chat_session_id": str(conversation_id)},
        )
    )
    async with asyncio.timeout(10):
        await hung.wait()
    return request


async def test_a_send_while_busy_is_a_409_and_changes_nothing(session):
    project = await seed_project(session, tools=["*"])
    conversation = await _conversation(session, project)

    with scripted_agent(project, [call("hang"), say("unreachable")]) as (agent, hung):
        with api_client(session, agent=agent) as (client, _):
            first = await _hung_turn(client, project, conversation, hung)

            busy = await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "again", "chat_session_id": str(conversation)},
            )
            assert busy.status_code == 409
            assert "already has a turn running" in busy.json()["detail"]
            assert busy.json()["code"] == "run_busy"
            assert chat_run.chat_run_registry.get(conversation) is not None

            stopped = await client.post(STOP.format(name=project.name, id=conversation))
            assert stopped.status_code == 202
            await first


async def test_stop_is_404_when_nothing_is_running(session):
    project = await seed_project(session, tools=["*"])
    conversation = await _conversation(session, project)

    with api_client(session) as (client, _):
        response = await client.post(STOP.format(name=project.name, id=conversation))

    assert response.status_code == 404


async def test_events_are_204_when_idle_and_404_for_an_unknown_conversation(session):
    project = await seed_project(session, tools=["*"])
    conversation = await _conversation(session, project)

    with api_client(session) as (client, _):
        idle = await client.get(EVENTS.format(name=project.name, id=conversation))
        unknown = await client.get(EVENTS.format(name=project.name, id=uuid.uuid4()))

    assert idle.status_code == 204
    assert unknown.status_code == 404


async def test_events_replay_from_the_start_and_resume_after_a_seq(session):
    project = await seed_project(session, tools=["*"])
    conversation = await _conversation(session, project)

    with scripted_agent(project, [call("hang"), say("unreachable")]) as (agent, hung):
        with api_client(session, agent=agent) as (client, _):
            turn = await _hung_turn(client, project, conversation, hung)
            url = EVENTS.format(name=project.name, id=conversation)
            watch_all = asyncio.create_task(client.get(url))
            watch_tail = asyncio.create_task(client.get(url, params={"after": 3}))
            await asyncio.sleep(0.05)
            await client.post(STOP.format(name=project.name, id=conversation))
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
    project = await seed_project(session, tools=["*"])
    hung_conversation = await _conversation(session, project)
    quiet = await _conversation(session, project)

    with scripted_agent(project, [call("hang"), say("unreachable")]) as (agent, hung):
        with api_client(session, agent=agent) as (client, _):
            turn = await _hung_turn(client, project, hung_conversation, hung)
            working = await client.get(STATUS.format(name=project.name))
            await client.post(
                STOP.format(name=project.name, id=hung_conversation),
            )
            await turn
            after_stop = await client.get(STATUS.format(name=project.name))

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
            )
            done = await client.get(STATUS.format(name=project.name))
            await client.put(
                SESSION.format(name=project.name),
                params={"chat_session_id": str(quiet)},
                json={"ack_run": True},
            )
            acked = await client.get(STATUS.format(name=project.name))

    assert done.json() == [
        {"chat_session_id": str(quiet), "status": "done", "unseen": True}
    ]
    assert acked.json() == []


async def test_the_chat_session_reports_run_state_and_ack_clears_the_events(session):
    project = await seed_project(session, tools=["*"])
    conversation = await _conversation(session, project)

    with scripted_agent(project, [say("finished")]) as (agent, _hung):
        with api_client(session, agent=agent) as (client, _):
            await client.post(
                RUN.format(name=project.name),
                json={"user_prompt": "hi", "chat_session_id": str(conversation)},
            )
            params = {"chat_session_id": str(conversation)}
            before = (
                await client.get(
                    SESSION.format(name=project.name),
                    params=params,
                )
            ).json()
            after = (
                await client.put(
                    SESSION.format(name=project.name),
                    params=params,
                    json={"ack_run": True, "message_history": []},
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
    project = await seed_project(session, tools=["*"])
    conversation = await _conversation(session, project)

    with scripted_agent(project, [call("hang"), say("unreachable")]) as (agent, hung):
        with api_client(session, agent=agent) as (client, _):
            turn = await _hung_turn(client, project, conversation, hung)
            live = (
                await client.get(
                    SESSION.format(name=project.name),
                    params={"chat_session_id": str(conversation)},
                )
            ).json()
            await client.post(STOP.format(name=project.name, id=conversation))
            await turn

    assert live["run_status"] == "working"


async def test_deleting_a_conversation_ends_its_run(session):
    project = await seed_project(session, tools=["*"])
    conversation = await _conversation(session, project)

    with scripted_agent(project, [call("hang"), say("unreachable")]) as (agent, hung):
        with api_client(session, agent=agent) as (client, _):
            turn = await _hung_turn(client, project, conversation, hung)
            deleted = await client.delete(
                SESSION.format(name=project.name),
                params={"chat_session_id": str(conversation)},
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
    project = await seed_project(session, tools=["*"])
    conversation = await _conversation(session, project)
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
    project = await seed_project(session, tools=["*"])
    conversation = await _conversation(session, project)

    with scripted_agent(project, [call("quick"), call("hang"), say("x")]) as (
        agent,
        hung,
    ):
        with api_client(session, agent=agent) as (client, _):
            async with api_module.lifespan(api_module.app):
                turn = await _hung_turn(client, project, conversation, hung)
            await turn  # the lifespan exit stopped it

    async with AsyncSession(engine) as other:
        row = await other.get(ChatSessionTable, conversation)
        assert row is not None
        assert (row.run_state, row.run_unseen) == ("interrupted", True)
        assert "job 42 submitted" in json.dumps(row.message_history)
        assert "Interrupted when VISTA quit." in json.dumps(row.message_history)


# -- displayed files ---------------------------------------------------------

DISPLAYED = "/projects/{name}/chat-sessions/{id}/displayed-files"


@pytest.fixture
def file_dirs(tmp_path, monkeypatch):
    uploads_dir = tmp_path / "uploads"
    output_dir = tmp_path / "output"
    uploads_dir.mkdir()
    output_dir.mkdir()
    agent = SimpleNamespace(uploads_dir=uploads_dir, output_dir=output_dir)
    monkeypatch.setattr(
        displayed_files_service, "project_agent_pool", FakeAgentPool(agent=agent)
    )
    return uploads_dir, output_dir


def _row(project, chat_id, kind, file, shown_at):
    """A record of `file` as it is now; call before changing or removing the file."""
    stat = file.stat()
    return DisplayedFileTable(
        project_id=project.id,
        chat_session_id=chat_id,
        run_id="run1",
        tool_call_id=f"call-{shown_at}",
        kind=kind,
        path=file.name,
        shown_at=shown_at,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )


async def _displayed(session, project, chat_id):
    with api_client(session) as (client, _):
        return await client.get(DISPLAYED.format(name=project.name, id=chat_id))


async def test_displayed_files_are_newest_first_with_each_status(session, file_dirs):
    uploads_dir, output_dir = file_dirs
    project = await seed_project(session)
    chat_id = await _conversation(session, project)
    current = output_dir / "plot.png"
    current.write_bytes(b"data")
    changed = uploads_dir / "table.csv"
    changed.write_bytes(b"a,b")
    gone = output_dir / "gone.txt"
    gone.write_bytes(b"x")
    session.add(_row(project, chat_id, "uploads", changed, "2026-01-01T10:00:02"))
    session.add(_row(project, chat_id, "outputs", gone, "2026-01-01T10:00:01"))
    session.add(_row(project, chat_id, "outputs", current, "2026-01-01T10:00:03"))
    await session.commit()
    changed.write_bytes(b"a,b,c,d")
    gone.unlink()

    response = await _displayed(session, project, chat_id)

    assert response.status_code == 200
    items = response.json()
    assert [(i["path"], i["status"]) for i in items] == [
        ("plot.png", "current"),
        ("table.csv", "changed"),
        ("gone.txt", "missing"),
    ]
    assert items[0] == {
        "kind": "outputs",
        "path": "plot.png",
        "name": "plot.png",
        "mime_type": "image/png",
        "run_id": "run1",
        "tool_call_id": "call-2026-01-01T10:00:03",
        "shown_at": "2026-01-01T10:00:03",
        "status": "current",
    }
    assert items[1]["kind"] == "uploads"


async def test_a_nested_path_is_named_by_its_last_segment(session, file_dirs):
    _, output_dir = file_dirs
    project = await seed_project(session)
    chat_id = await _conversation(session, project)
    nested = output_dir / "run1" / "spectrum.weird"
    nested.parent.mkdir()
    nested.write_bytes(b"x")
    row = _row(project, chat_id, "outputs", nested, "2026-01-01T10:00:00")
    row.path = "run1/spectrum.weird"
    session.add(row)
    await session.commit()

    (item,) = (await _displayed(session, project, chat_id)).json()

    assert item["name"] == "spectrum.weird"
    assert item["path"] == "run1/spectrum.weird"
    assert item["mime_type"] == "application/octet-stream"
    assert item["status"] == "current"


async def test_a_same_size_rewrite_with_another_mtime_is_changed(session, file_dirs):
    _, output_dir = file_dirs
    project = await seed_project(session)
    chat_id = await _conversation(session, project)
    plot = output_dir / "plot.png"
    plot.write_bytes(b"data")
    session.add(_row(project, chat_id, "outputs", plot, "2026-01-01T10:00:00"))
    await session.commit()
    mtime_ns = plot.stat().st_mtime_ns
    os.utime(plot, ns=(mtime_ns + 1_000_000, mtime_ns + 1_000_000))

    (item,) = (await _displayed(session, project, chat_id)).json()

    assert item["status"] == "changed"


async def test_a_chat_that_displayed_nothing_has_no_rows(session, file_dirs):
    project = await seed_project(session)
    chat_id = await _conversation(session, project)

    response = await _displayed(session, project, chat_id)

    assert response.status_code == 200
    assert response.json() == []


async def test_displayed_files_only_list_that_chats_rows(session, file_dirs):
    _, output_dir = file_dirs
    project = await seed_project(session)
    mine = await _conversation(session, project)
    other = await _conversation(session, project)
    plot = output_dir / "plot.png"
    plot.write_bytes(b"data")
    session.add(_row(project, other, "outputs", plot, "2026-01-01T10:00:00"))
    await session.commit()

    assert (await _displayed(session, project, mine)).json() == []


async def test_a_chat_of_another_project_is_404(session, file_dirs):
    project = await seed_project(session)
    elsewhere = await seed_project(session)
    chat_id = await _conversation(session, elsewhere)

    assert (await _displayed(session, project, chat_id)).status_code == 404
    assert (await _displayed(session, project, uuid.uuid4())).status_code == 404
