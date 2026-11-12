"""
The Background tab's routes (openspec change background-tasks, task 7.1): a
conversation's tasks, stopping one, and the end of a command's log.
"""

import uuid

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from harness import FakeSandbox, api_client, seed_project
from vista_backend.config import settings
from vista_backend.services import background_tasks, inbox
from vista_backend.services import chat_session as chat_session_service

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

TASKS = "/projects/{name}/chat-sessions/{id}/tasks"
STOP = "/projects/{name}/chat-sessions/{id}/tasks/{task}/stop"
LOG = "/projects/{name}/chat-sessions/{id}/tasks/{task}/log"


@pytest.fixture
def env(session, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(inbox, "_listeners", [])
    registry = background_tasks.BackgroundTaskRegistry()
    monkeypatch.setattr(background_tasks, "background_task_registry", registry)
    return registry


async def _conversation(session, project) -> uuid.UUID:
    row = await chat_session_service.create_chat_session(session, project_id=project.id)
    row_id = row.id
    await session.commit()
    return row_id


async def _command(session, project, conversation, command="sleep 600"):
    async with AsyncSession(session.bind) as db:
        return await background_tasks.create_command(
            db, chat_session_id=conversation, project_id=project.id, command=command
        )


def _write_log(project, record, text: str):
    path = background_tasks.host_log_path(
        settings.data_dir / "volumes" / str(project.id), record
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def test_a_conversation_lists_its_own_tasks_only(session, env):
    project = await seed_project(session)
    mine, theirs = (
        await _conversation(session, project),
        await _conversation(session, project),
    )
    command = await _command(session, project, mine, "python fit.py")
    async with AsyncSession(session.bind) as db:
        watch = await background_tasks.create_watch(
            db,
            chat_session_id=mine,
            project_id=project.id,
            job_id="4242",
            cluster="odo",
        )
    await _command(session, project, theirs, "not mine")

    with api_client(session) as (client, _):
        listed = await client.get(TASKS.format(name=project.name, id=mine))

    assert listed.status_code == 200
    body = listed.json()
    assert [(t["label"], t["kind"], t["state"]) for t in body] == [
        ("bg-1", "command", "running"),
        ("watch-2", "job_watch", "watching"),
    ]
    assert body[0]["command"] == "python fit.py"
    assert body[0]["log_path"] == command.log_path
    assert body[1]["job_id"] == "4242" and body[1]["cluster"] == "odo"
    assert all(t["runtime_s"] >= 0 for t in body)
    assert str(watch.id) == body[1]["id"]


async def test_a_paused_watch_says_why(session, env):
    project = await seed_project(session)
    conversation = await _conversation(session, project)
    async with AsyncSession(session.bind) as db:
        watch = await background_tasks.create_watch(
            db,
            chat_session_id=conversation,
            project_id=project.id,
            job_id="1",
            cluster="odo",
        )
        row = await db.get(background_tasks.BackgroundTaskTable, watch.id)
        row.state, row.paused_reason = "paused", "Reconnect Odo in Settings"
        await db.commit()

    with api_client(session) as (client, _):
        [task] = (
            await client.get(TASKS.format(name=project.name, id=conversation))
        ).json()

    assert (task["state"], task["paused_reason"]) == (
        "paused",
        "Reconnect Odo in Settings",
    )


async def test_stop_ends_a_running_command_through_its_watcher(session, env):
    project = await seed_project(session)
    conversation = await _conversation(session, project)
    record = await _command(session, project, conversation)
    sandbox = FakeSandbox()
    sandbox.start(str(record.id))
    await env.watch_command(
        record,
        call=sandbox,
        volume_root=settings.data_dir / "volumes" / str(project.id),
    )

    with api_client(session) as (client, _):
        stopped = await client.post(
            STOP.format(name=project.name, id=conversation, task=record.id)
        )
        again = await client.post(
            STOP.format(name=project.name, id=conversation, task=record.id)
        )

    assert stopped.status_code == 200 and stopped.json()["state"] == "stopped"
    assert "signal_task" in sandbox.names_called()
    assert again.json()["state"] == "stopped", "an ended task is returned as it is"


async def test_another_conversations_task_is_not_found(session, env):
    project = await seed_project(session)
    mine, theirs = (
        await _conversation(session, project),
        await _conversation(session, project),
    )
    record = await _command(session, project, theirs)
    _write_log(project, record, "secret\n")

    with api_client(session) as (client, _):
        stop = await client.post(
            STOP.format(name=project.name, id=mine, task=record.id)
        )
        log = await client.get(LOG.format(name=project.name, id=mine, task=record.id))

    assert stop.status_code == 404 and log.status_code == 404


async def test_the_log_route_sends_the_end_of_the_log(session, env, monkeypatch):
    from vista_backend.api import chat_runs

    monkeypatch.setattr(chat_runs, "LOG_TAIL_BYTES", 40)
    project = await seed_project(session)
    conversation = await _conversation(session, project)
    record = await _command(session, project, conversation)
    _write_log(project, record, "".join(f"line {n}\n" for n in range(1, 21)))

    with api_client(session) as (client, _):
        log = await client.get(
            LOG.format(name=project.name, id=conversation, task=record.id)
        )

    assert log.status_code == 200
    assert log.headers["content-type"].startswith("text/plain")
    lines = log.text.splitlines()
    assert lines[-1] == "line 20"
    assert all(line.startswith("line ") and len(line) > 5 for line in lines), (
        "cut at a line start, not inside one"
    )


async def test_a_watch_or_a_missing_log_has_no_log(session, env):
    project = await seed_project(session)
    conversation = await _conversation(session, project)
    record = await _command(session, project, conversation)
    async with AsyncSession(session.bind) as db:
        watch = await background_tasks.create_watch(
            db,
            chat_session_id=conversation,
            project_id=project.id,
            job_id="1",
            cluster="odo",
        )

    with api_client(session) as (client, _):
        not_yet = await client.get(
            LOG.format(name=project.name, id=conversation, task=record.id)
        )
        a_watch = await client.get(
            LOG.format(name=project.name, id=conversation, task=watch.id)
        )

    assert not_yet.status_code == 404 and a_watch.status_code == 404
