"""
Job watches (`services/background_tasks.py`, openspec change background-tasks,
tasks 5.2-5.4): what a watch reports, how often it reads, and how it waits for
the researcher when cluster access is lost.

Readings come from a scripted reader (`read_hpc_job_state`'s answers) on a fake
clock; records and the inbox they report to are a real SQLite file.
"""

import asyncio
import uuid
from datetime import datetime

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from harness import seed_project
from vista_backend.db.schemas import LocalSettingsUpdate
from vista_backend.services import background_tasks, chat_run, inbox, local_settings
from vista_backend.services import chat_session as chat_session_service

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


class FakeClock(background_tasks.Clock):
    """
    Time that moves only when a watch sleeps. With `blocking`, a sleep waits
    for its wake events instead, as a paused watch waits for a Settings save.
    """

    def __init__(self, start: float) -> None:
        self.t = start
        self.sleeps: list[float] = []
        self.blocking = False

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float, *wake: asyncio.Event) -> None:
        self.sleeps.append(seconds)
        if self.blocking:
            await super().sleep(3600, *wake)
            return
        self.t += seconds
        await asyncio.sleep(0)


class Script:
    """Answers readings in order; past the end, a reading never returns."""

    def __init__(self, *readings: dict) -> None:
        self.readings = list(readings)
        self.read = 0

    async def __call__(self, record) -> dict:
        if not self.readings:
            await asyncio.Event().wait()
        self.read += 1
        return self.readings.pop(0)


def state(s: str, **extra) -> dict:
    return {"state": s, **extra}


LOST = {"error": "access_lost", "cluster": "frontier", "message": "rejected"}


@pytest.fixture
def records(engine, monkeypatch):
    monkeypatch.setattr(
        background_tasks, "session_factory", lambda: AsyncSession(engine)
    )
    monkeypatch.setattr(inbox, "_listeners", [])  # results only queue
    return engine


@pytest.fixture
async def registry(records, monkeypatch):
    registry = background_tasks.BackgroundTaskRegistry()
    monkeypatch.setattr(background_tasks, "background_task_registry", registry)
    yield registry
    await registry.shutdown()


@pytest.fixture
async def chat(session) -> tuple[uuid.UUID, uuid.UUID]:
    project = await seed_project(session)
    row = await chat_session_service.create_chat_session(session, project_id=project.id)
    ids = (row.id, project.id)
    await session.commit()
    return ids


async def _watch(engine, chat, cluster="frontier", job_id="4242"):
    async with AsyncSession(engine) as db:
        return await background_tasks.create_watch(
            db,
            chat_session_id=chat[0],
            project_id=chat[1],
            job_id=job_id,
            cluster=cluster,
        )


def _clock_for(record) -> FakeClock:
    return FakeClock(datetime.fromisoformat(record.created_at).timestamp())


async def _run(registry, record, script, clock=None):
    clock = clock or _clock_for(record)
    registry.watch_job(record, read=script, clock=clock)
    async with asyncio.timeout(10):
        await registry.wait(record.id)
    return clock


async def _events(engine, chat_id) -> list:
    async with AsyncSession(engine) as db:
        return await inbox.list_queued(db, chat_id)


async def _record(engine, record):
    async with AsyncSession(engine) as db:
        return await background_tasks.get(db, record.id)


async def _until(condition, timeout: float = 10) -> None:
    async with asyncio.timeout(timeout):
        while not await condition():
            await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# What a watch reports
# ---------------------------------------------------------------------------


async def test_a_start_and_an_end_are_each_reported_once(registry, records, chat):
    record = await _watch(records, chat)

    await _run(
        registry,
        record,
        Script(
            state("PENDING"),
            state("RUNNING"),
            state("RUNNING"),
            state("COMPLETED", exit_code=0),
        ),
    )

    events = await _events(records, chat[0])
    assert [e.payload["event"] for e in events] == ["started", "ended"]
    assert events[0].text == "Job 4242 on Frontier (watch-1) started running."
    assert events[1].text.startswith(
        "Job 4242 on Frontier (watch-1) has ended: COMPLETED, exit code 0."
    )
    assert "get_hpc_job_status" in events[1].text
    assert events[1].payload["summary"] == "watch-1: job 4242 on Frontier completed"
    saved = await _record(records, record)
    assert (saved.state, saved.last_job_state, saved.started_reported) == (
        "ended",
        "COMPLETED",
        True,
    )
    assert saved.ended_at is not None


async def test_a_failure_is_reported_once_a_second_reading_confirms_it(
    registry, records, chat
):
    record = await _watch(records, chat)

    clock = await _run(
        registry,
        record,
        Script(state("RUNNING"), state("FAILED"), state("FAILED", exit_code=7)),
    )

    events = await _events(records, chat[0])
    assert [e.payload["event"] for e in events] == ["started", "ended"]
    assert "has ended: FAILED, exit code 7" in events[1].text
    assert clock.sleeps[1] == background_tasks.FAST_POLL_S, "checked again soon"


async def test_a_single_failed_reading_is_not_believed(registry, records, chat):
    record = await _watch(records, chat)

    await _run(
        registry,
        record,
        Script(state("RUNNING"), state("FAILED"), state("RUNNING"), state("COMPLETED")),
    )

    events = await _events(records, chat[0])
    assert [e.payload["job_state"] for e in events] == ["RUNNING", "COMPLETED"]
    assert not any("FAILED" in e.text for e in events)


async def test_a_job_cancelled_in_the_queue_is_reported_once_for_its_end(
    registry, records, chat
):
    record = await _watch(records, chat)

    await _run(
        registry, record, Script(state("PENDING"), state("CANCELED"), state("CANCELED"))
    )

    [event] = await _events(records, chat[0])
    assert (
        event.payload["event"] == "ended" and event.payload["job_state"] == "CANCELED"
    )


async def test_a_facility_hiccup_is_read_through(registry, records, chat):
    record = await _watch(records, chat)

    await _run(
        registry,
        record,
        Script(
            state("RUNNING"),
            {"error": "failed", "message": "HTTP 502"},
            state("COMPLETED"),
        ),
    )

    assert [e.payload["event"] for e in await _events(records, chat[0])] == [
        "started",
        "ended",
    ]


# ---------------------------------------------------------------------------
# How often it reads
# ---------------------------------------------------------------------------


async def test_a_long_queue_wait_is_read_every_five_minutes(registry, records, chat):
    record = await _watch(records, chat)
    clock = _clock_for(record)
    start = clock.t

    await _run(
        registry, record, Script(*[state("PENDING")] * 40, state("COMPLETED")), clock
    )

    assert clock.sleeps[:20] == [30.0] * 20, "every 30 s for the first 10 minutes"
    assert clock.sleeps[20:24] == [60.0, 120.0, 240.0, 300.0], "then lengthening"
    after_an_hour = [s for s, t in _times(start, clock.sleeps) if t >= start + 3600]
    assert after_an_hour and set(after_an_hour) == {300.0}


def _times(start: float, sleeps: list[float]) -> list[tuple[float, float]]:
    """Each sleep with the time it began."""
    out, t = [], start
    for s in sleeps:
        out.append((s, t))
        t += s
    return out


async def test_a_job_that_starts_running_is_read_every_30_seconds_again(
    registry, records, chat
):
    record = await _watch(records, chat)

    clock = await _run(
        registry,
        record,
        Script(*[state("PENDING")] * 24, *[state("RUNNING")] * 21, state("COMPLETED")),
    )

    assert clock.sleeps[23] == 300.0, "slowed down in the queue"
    assert clock.sleeps[24:44] == [30.0] * 20, "fast for 10 minutes after the start"
    assert clock.sleeps[44] == 60.0


async def test_a_429_is_waited_out_as_long_as_the_facility_asks(
    registry, records, chat
):
    record = await _watch(records, chat)

    clock = await _run(
        registry,
        record,
        Script(
            {"error": "rate_limited", "retry_after": 120, "message": "slow down"},
            {"error": "rate_limited", "retry_after": None, "message": "slow down"},
            state("COMPLETED"),
        ),
    )

    assert clock.sleeps[:2] == [120.0, background_tasks.SLOW_POLL_S]


# ---------------------------------------------------------------------------
# Lost access
# ---------------------------------------------------------------------------


async def test_lost_access_pauses_without_a_word_to_the_agent(
    registry, records, chat, session
):
    record = await _watch(records, chat)
    clock = _clock_for(record)
    clock.blocking = True
    registry.watch_job(record, read=Script(LOST), clock=clock)

    async def paused():
        return (await _record(records, record)).state == "paused"

    await _until(paused)
    saved = await _record(records, record)
    assert saved.paused_reason == "Reconnect Frontier in Settings"
    assert await _events(records, chat[0]) == [], "no result, so no agent turn"
    assert clock.sleeps == [background_tasks.PAUSED_RETRY_S]
    async with AsyncSession(records) as db:
        assert await inbox.waiting_on_researcher(db, [chat[0]]) == {chat[0]}
        row = await chat_session_service.get_chat_session(
            db, project_id=chat[1], chat_session_id=chat[0]
        )
        assert chat_run.row_status(row, waiting=True) == "needs_you"


async def test_a_paused_watch_retries_every_five_minutes(registry, records, chat):
    record = await _watch(records, chat)

    clock = await _run(registry, record, Script(LOST, LOST, LOST, state("COMPLETED")))

    assert clock.sleeps == [300.0, 300.0, 300.0]


async def test_reconnecting_in_settings_resumes_the_watch_and_later_changes_are_reported(
    registry, records, chat, session
):
    record = await _watch(records, chat)
    clock = _clock_for(record)
    clock.blocking = True
    script = Script(LOST, state("RUNNING"))
    registry.watch_job(record, read=script, clock=clock)

    async def paused():
        return (await _record(records, record)).state == "paused"

    await _until(paused)

    # The researcher saves a new token: the watch reads again at once.
    await local_settings.update(
        session, LocalSettingsUpdate(frontier_s3m_token="fresh")
    )
    await session.commit()

    async def resumed():
        return (await _record(records, record)).state == "watching"

    await _until(resumed)
    saved = await _record(records, record)
    assert saved.paused_reason is None
    [event] = await _events(records, chat[0])
    assert event.payload["event"] == "started", "the pause itself was never reported"
    async with AsyncSession(records) as db:
        assert await inbox.waiting_on_researcher(db, [chat[0]]) == {chat[0]}, (
            "the started result waits; the pause no longer does"
        )


# ---------------------------------------------------------------------------
# Limits, stopping, and the reading itself
# ---------------------------------------------------------------------------


async def test_a_ninth_watch_is_refused_naming_the_limit(records, chat):
    for n in range(8):
        await _watch(records, chat, job_id=str(n))

    with pytest.raises(background_tasks.TaskRefused, match="8 jobs"):
        await _watch(records, chat, job_id="9")


async def test_an_unknown_cluster_is_refused(records, chat):
    with pytest.raises(
        background_tasks.TaskRefused, match="odo, frontier, lux, perlmutter"
    ):
        await _watch(records, chat, cluster="summit")


async def test_stopping_a_watch_drops_it_and_reports_nothing(registry, records, chat):
    record = await _watch(records, chat)
    clock = _clock_for(record)
    clock.blocking = True
    registry.watch_job(record, read=Script(state("PENDING")), clock=clock)
    await _until(lambda: _sleeping(clock))

    stopped = await registry.stop(record.id)

    assert stopped is not None and stopped.state == "stopped"
    assert await _events(records, chat[0]) == []


async def _sleeping(clock: FakeClock) -> bool:
    return bool(clock.sleeps)


async def test_each_reading_carries_the_credentials_settings_hold_then(
    records, chat, session, monkeypatch
):
    sent: list[dict] = []

    class Server:
        async def direct_call_tool(self, name, args, metadata):
            sent.append({"name": name, "args": args, "metadata": metadata})
            return {"state": "PENDING"}

    from vista_backend.agents import agents

    monkeypatch.setattr(agents, "get_vista_mcp_server", lambda: Server())
    record = await _watch(records, chat)
    read = background_tasks.mcp_state_reader()

    await local_settings.update(
        session, LocalSettingsUpdate(frontier_s3m_token="first")
    )
    await session.commit()
    assert await read(record) == {"state": "PENDING"}
    await local_settings.update(
        session, LocalSettingsUpdate(frontier_s3m_token="second")
    )
    await session.commit()
    await read(record)

    assert [s["name"] for s in sent] == ["read_hpc_job_state"] * 2
    assert sent[0]["args"] == {"job_id": "4242", "cluster": "frontier"}
    tokens = [s["metadata"]["vista"]["user"].get("frontier_s3m_token") for s in sent]
    assert tokens == ["first", "second"]
    assert sent[0]["metadata"]["vista"]["project_paths"]["output_dir"].endswith(
        f"{chat[1]}/data/output"
    )


async def test_a_failed_call_reads_as_a_failure(records, chat, monkeypatch):
    from pydantic_ai.exceptions import ModelRetry

    class Server:
        async def direct_call_tool(self, name, args, metadata):
            raise ModelRetry("unknown tool")

    from vista_backend.agents import agents

    monkeypatch.setattr(agents, "get_vista_mcp_server", lambda: Server())
    record = await _watch(records, chat)

    assert await background_tasks.mcp_state_reader()(record) == {
        "error": "failed",
        "message": "unknown tool",
    }


async def test_the_real_clock_wakes_on_any_one_event():
    stop, nudge = asyncio.Event(), asyncio.Event()
    clock = background_tasks.Clock()
    asyncio.get_running_loop().call_later(0.05, nudge.set)

    async with asyncio.timeout(5):
        await clock.sleep(3600, stop, nudge)

    assert nudge.is_set() and not stop.is_set()


async def test_a_watch_stops_even_while_a_reading_is_held(registry, records, chat):
    record = await _watch(records, chat)
    registry.watch_job(record, read=Script(), clock=_clock_for(record))  # never answers
    await asyncio.sleep(0)

    async with asyncio.timeout(5):
        stopped = await registry.stop(record.id)

    assert stopped is not None and stopped.state == "stopped"


async def test_a_job_that_ended_while_the_watch_was_paused_is_reported(
    registry, records, chat
):
    """Found trying it: the reading that resumed the watch also ended it, and crashed."""
    record = await _watch(records, chat)

    await _run(
        registry,
        record,
        Script(state("RUNNING"), LOST, state("COMPLETED", exit_code=0)),
    )

    saved = await _record(records, record)
    assert (saved.state, saved.paused_reason) == ("ended", None)
    assert [e.payload["event"] for e in await _events(records, chat[0])] == [
        "started",
        "ended",
    ]


async def test_active_is_running(registry, records, chat):
    """S3M reports a running job as ACTIVE (PSI/J), not RUNNING."""
    record = await _watch(records, chat)

    await _run(
        registry, record, Script(state("QUEUED"), state("ACTIVE"), state("COMPLETED"))
    )

    events = await _events(records, chat[0])
    assert [e.payload["event"] for e in events] == ["started", "ended"]


async def test_a_watch_that_crashes_does_not_stay_paused(registry, records, chat):
    record = await _watch(records, chat)

    clock = _clock_for(record)
    original = clock.sleep
    calls = {"n": 0}

    async def sleep_then_explode(seconds, *wake):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")  # anything the loop did not expect
        await original(seconds, *wake)

    clock.sleep = sleep_then_explode  # type: ignore[method-assign]
    await _run(registry, record, Script(LOST, LOST, LOST), clock)

    saved = await _record(records, record)
    assert saved.state == "stopped" and saved.paused_reason is None
    async with AsyncSession(records) as db:
        assert await inbox.waiting_on_researcher(db, [chat[0]]) == set()


# ---------------------------------------------------------------------------
# Startup (task 6.1)
# ---------------------------------------------------------------------------


async def test_a_job_that_finished_while_vista_was_closed_is_reported(
    registry, records, chat
):
    watching = await _watch(records, chat, job_id="111")
    paused = await _watch(records, chat, job_id="222")
    async with AsyncSession(records) as db:
        row = await db.get(background_tasks.BackgroundTaskTable, paused.id)
        row.state, row.paused_reason = "paused", "Reconnect Frontier in Settings"
        await db.commit()
    readings = {"111": state("COMPLETED", exit_code=0), "222": state("RUNNING")}
    read_at: list[str] = []

    async def read(record):
        read_at.append(record.job_id)
        reading = readings.pop(record.job_id, None)
        if reading is None:
            await asyncio.Event().wait()
        return reading

    interrupted, resumed = await background_tasks.recover_at_startup(read=read)

    assert (interrupted, resumed) == (0, 2)
    await registry.wait(watching.id)
    assert (await _record(records, watching)).state == "ended"

    async def resumed_watch():
        return (await _record(records, paused)).state == "watching"

    await _until(resumed_watch)
    assert sorted(read_at[:2]) == ["111", "222"], "each read at once"
    events = {
        e.payload["job_id"]: e.payload["event"] for e in await _events(records, chat[0])
    }
    assert events == {"111": "ended", "222": "started"}
