"""
Background commands' records, limits and watcher (`services/background_tasks.py`,
openspec change background-tasks, tasks 4.1 and 4.3).

The watcher talks to a fake dev MCP server (`harness.FakeSandbox`); records and
the inbox it reports to are a real SQLite file.
"""

import asyncio
import contextlib
import uuid
from pathlib import Path

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from harness import FakeSandbox, seed_project
from vista_backend.services import background_tasks, inbox
from vista_backend.services import chat_session as chat_session_service

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
def records(engine, monkeypatch):
    monkeypatch.setattr(
        background_tasks, "session_factory", lambda: AsyncSession(engine)
    )
    monkeypatch.setattr(inbox, "_listeners", [])  # no registry: results only queue
    return engine


@pytest.fixture
async def registry(records):
    registry = background_tasks.BackgroundTaskRegistry()
    yield registry
    await registry.shutdown()


@pytest.fixture
async def chat(session) -> tuple[uuid.UUID, uuid.UUID]:
    project = await seed_project(session)
    row = await chat_session_service.create_chat_session(session, project_id=project.id)
    ids = (row.id, project.id)
    await session.commit()
    return ids


async def _command(engine, chat, command="sleep 60", timeout=None):
    chat_id, project_id = chat
    async with AsyncSession(engine) as db:
        return await background_tasks.create_command(
            db,
            chat_session_id=chat_id,
            project_id=project_id,
            command=command,
            timeout=timeout,
        )


async def _events(engine, chat_id) -> list:
    async with AsyncSession(engine) as db:
        return await inbox.list_queued(db, chat_id)


# ---------------------------------------------------------------------------
# Records and limits
# ---------------------------------------------------------------------------


async def test_a_command_is_recorded_running_with_its_log(records, chat):
    record = await _command(records, chat)

    assert record.label == "bg-1"
    assert record.kind == "command" and record.state == "running"
    assert record.timeout_s == 3600, "an hour unless the agent sets one"
    assert record.log_path == f"/mnt/data/tasks/{record.id}.log"
    assert (await _command(records, chat)).label == "bg-2"


async def test_a_fifth_command_is_refused_naming_the_limit(records, chat):
    running = [await _command(records, chat) for _ in range(4)]

    with pytest.raises(background_tasks.TaskRefused, match="4 background commands"):
        await _command(records, chat)

    async with AsyncSession(records) as db:
        states = [(await background_tasks.get(db, r.id)).state for r in running]
    assert states == ["running"] * 4, "the running commands are unaffected"

    await background_tasks.record_end(running[0].id, "exited", 0)
    assert (await _command(records, chat)).state == "running", "one ended: room again"


@pytest.mark.parametrize("timeout", [48 * 3600, 86_401])
async def test_a_limit_above_the_ceiling_is_refused_naming_it(records, chat, timeout):
    with pytest.raises(background_tasks.TaskRefused, match="24 hours"):
        await _command(records, chat, timeout=timeout)


@pytest.mark.parametrize("timeout", [0, -5, "soon"])
async def test_a_nonsense_limit_is_refused(records, chat, timeout):
    with pytest.raises(background_tasks.TaskRefused):
        await _command(records, chat, timeout=timeout)


async def test_an_end_is_recorded(records, chat):
    record = await _command(records, chat)

    ended = await background_tasks.record_end(record.id, "exited", 7)

    assert ended is not None
    assert (ended.state, ended.exit_status) == ("exited", 7)
    assert ended.ended_at is not None


# ---------------------------------------------------------------------------
# The watcher
# ---------------------------------------------------------------------------


def _log(volume: Path, record, text: str) -> None:
    path = background_tasks.host_log_path(volume, record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def _watch(registry, record, sandbox, volume, hold=None):
    sandbox.start(str(record.id))
    await registry.watch_command(record, call=sandbox, volume_root=volume, hold=hold)


async def test_a_non_zero_exit_is_reported_with_the_last_lines(
    registry, records, chat, tmp_path
):
    record = await _command(records, chat, command="python fit.py")
    _log(tmp_path, record, "".join(f"step {n}\n" for n in range(1, 31)) + "diverged\n")
    sandbox = FakeSandbox()
    await _watch(registry, record, sandbox, tmp_path)

    sandbox.exit(str(record.id), 7)
    await registry.wait(record.id)

    async with AsyncSession(records) as db:
        saved = await background_tasks.get(db, record.id)
    assert (saved.state, saved.exit_status) == ("exited", 7)
    [event] = await _events(records, chat[0])
    assert event.kind == "task_event" and event.wakes is True
    assert event.text.startswith("Background task bg-1 exited with status 7 after ")
    assert "Command: python fit.py" in event.text
    assert f"Log: /mnt/data/tasks/{record.id}.log" in event.text
    assert event.text.endswith("step 30\ndiverged"), "the last lines, not the first"
    assert "step 10\n" not in event.text
    assert event.payload["summary"].startswith("bg-1 finished · exit 7 · ")
    assert event.payload["state"] == "exited" and event.payload["exit_status"] == 7
    assert sandbox.names_called() == ["wait_task"]


async def test_a_command_at_its_limit_is_ended_and_recorded_as_timed_out(
    registry, records, chat, tmp_path
):
    record = await _command(records, chat, timeout=1)
    sandbox = FakeSandbox()
    await _watch(registry, record, sandbox, tmp_path)

    async with asyncio.timeout(10):
        await registry.wait(record.id)

    async with AsyncSession(records) as db:
        assert (await background_tasks.get(db, record.id)).state == "timed_out"
    assert sandbox.names_called()[-1] == "signal_task"
    [event] = await _events(records, chat[0])
    assert "was stopped at its time limit of 1s" in event.text
    assert "It printed nothing." in event.text


async def test_a_lost_server_is_recorded_as_interrupted(
    registry, records, chat, tmp_path
):
    record = await _command(records, chat)
    sandbox = FakeSandbox()
    await _watch(registry, record, sandbox, tmp_path)

    sandbox.lose(str(record.id))
    await registry.wait(record.id)

    async with AsyncSession(records) as db:
        assert (await background_tasks.get(db, record.id)).state == "interrupted"
    [event] = await _events(records, chat[0])
    assert "was interrupted" in event.text


async def test_a_command_gone_without_a_signal_from_here_is_interrupted(
    registry, records, chat, tmp_path
):
    record = await _command(records, chat)
    sandbox = FakeSandbox()
    await _watch(registry, record, sandbox, tmp_path)

    sandbox.tasks[str(record.id)].ended.set()  # its group vanished, with no `.exit`
    await registry.wait(record.id)

    async with AsyncSession(records) as db:
        assert (await background_tasks.get(db, record.id)).state == "interrupted"


async def test_stop_signals_the_command_and_records_it_stopped(
    registry, records, chat, tmp_path
):
    record = await _command(records, chat)
    sandbox = FakeSandbox()
    await _watch(registry, record, sandbox, tmp_path)
    await asyncio.sleep(0)

    stopped = await registry.stop(record.id)

    assert stopped is not None and stopped.state == "stopped"
    assert "signal_task" in sandbox.names_called()
    [event] = await _events(records, chat[0])
    assert "Background task bg-1 was stopped after" in event.text
    assert not registry.watching(record.id)


async def test_a_running_record_nothing_watches_is_stopped_as_interrupted(
    registry, records, chat
):
    record = await _command(records, chat)

    stopped = await registry.stop(record.id)

    assert stopped is not None and stopped.state == "interrupted"


async def test_the_watcher_holds_what_it_is_given_until_the_end(
    registry, records, chat, tmp_path
):
    held: list[str] = []

    @contextlib.asynccontextmanager
    async def hold():
        held.append("taken")
        yield
        held.append("released")

    record = await _command(records, chat)
    sandbox = FakeSandbox()
    await _watch(registry, record, sandbox, tmp_path, hold=hold())
    assert held == ["taken"], "taken before the turn that started it can let go"

    sandbox.exit(str(record.id), 0)
    await registry.wait(record.id)
    assert held == ["taken", "released"]


async def test_a_result_for_a_deleted_conversation_is_dropped(
    registry, records, chat, session, tmp_path
):
    record = await _command(records, chat)
    sandbox = FakeSandbox()
    await _watch(registry, record, sandbox, tmp_path)
    await chat_session_service.delete_chat_session(
        session, project_id=chat[1], chat_session_id=chat[0]
    )
    await session.commit()

    sandbox.exit(str(record.id), 0)
    await registry.wait(record.id)

    async with AsyncSession(records) as db:
        assert await background_tasks.get(db, record.id) is None


def test_runtimes_read_as_a_person_would_say_them():
    assert background_tasks.format_runtime(4) == "4s"
    assert background_tasks.format_runtime(242) == "4m 2s"
    assert background_tasks.format_runtime(3 * 3600 + 120) == "3h 2m"


async def test_commands_started_in_one_step_are_counted_one_at_a_time(records, chat):
    """
    A model step's tool calls run concurrently. Five starts at once must still
    give four commands, labelled apart, and one refusal (found trying it: all
    five ran, every one `bg-1`).
    """
    chat_id, project_id = chat

    async def start():
        return await background_tasks.start_command(
            chat_session_id=chat_id,
            project_id=project_id,
            tool_args={"command": "sleep 300", "background": True},
        )

    results = await asyncio.gather(*(start() for _ in range(5)), return_exceptions=True)

    started = [r.record.label for r in results if not isinstance(r, Exception)]
    refused = [r for r in results if isinstance(r, background_tasks.TaskRefused)]
    assert sorted(started) == ["bg-1", "bg-2", "bg-3", "bg-4"]
    assert len(refused) == 1


async def test_watches_started_in_one_step_are_counted_one_at_a_time(records, chat):
    chat_id, project_id = chat

    async def watch(n):
        async with AsyncSession(records) as db:
            return await background_tasks.create_watch(
                db,
                chat_session_id=chat_id,
                project_id=project_id,
                job_id=str(n),
                cluster="odo",
            )

    results = await asyncio.gather(
        *(watch(n) for n in range(9)), return_exceptions=True
    )

    labels = [r.label for r in results if not isinstance(r, Exception)]
    assert len(labels) == 8 and len(set(labels)) == 8
    assert sum(isinstance(r, background_tasks.TaskRefused) for r in results) == 1
