"""Job notifications: once, to the requester, by DM or in the thread."""

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.db.schemas import HpcJobRecordTable
from vista_backend.slack import store
from vista_backend.slack.jobs import JobMonitor, elapsed

from .conftest import TEAM, link
from .fakes import FakeTransport

pytestmark = pytest.mark.anyio


class Poll:
    """A scripted `get_hpc_job_status`: job id → state, or an exception."""

    def __init__(self, states):
        self.states = states
        self.calls: list[str] = []

    async def __call__(self, session, record):
        self.calls.append(record.job_id)
        state = self.states[record.job_id]
        if isinstance(state, Exception):
            raise state
        return state


async def _record(session, user, job_id="4471", *, origin="slack", **kw):
    row = HpcJobRecordTable(
        user_id=user.id,
        origin=origin,
        cluster="frontier",
        job_id=job_id,
        job="flibe-md",
        submitted_at="2026-10-16T10:00:00+00:00",
        **(
            {
                "slack_team_id": TEAM,
                "slack_channel_id": "C1",
                "slack_thread_ts": "10.0",
                "slack_user_id": "U1",
            }
            if origin == "slack"
            else {}
        ),
        **kw,
    )
    record_id = row.id
    session.add(row)
    await session.commit()
    return record_id


def _monitor(engine, slack, poll) -> JobMonitor:
    return JobMonitor(slack, poll=poll, session_factory=lambda: AsyncSession(engine))


async def _get(engine, record_id) -> HpcJobRecordTable:
    async with AsyncSession(engine) as s:
        row = await s.get(HpcJobRecordTable, record_id)
        assert row is not None
        return row


async def test_a_finished_slack_job_is_sent_by_dm_once(engine, session, alice):
    await link(session, alice, "U1")
    rid = await _record(session, alice)
    slack = FakeTransport()
    poll = Poll({"4471": "COMPLETED"})
    monitor = _monitor(engine, slack, poll)

    assert await monitor.reconcile_once() == 1
    assert await monitor.reconcile_once() == 0

    (msg,) = slack.messages
    assert msg.channel == "DU1" and msg.thread_ts is None
    assert "<@U1>" in msg.text and "4471" in msg.text and "frontier" in msg.text
    assert "COMPLETED" in msg.text
    row = await _get(engine, rid)
    assert row.state == "COMPLETED" and row.notified_at and row.finished_at
    assert poll.calls == ["4471"], "a notified job is not polled again"


async def test_a_running_job_waits(engine, session, alice):
    await link(session, alice, "U1")
    rid = await _record(session, alice)
    slack = FakeTransport()

    assert (
        await _monitor(engine, slack, Poll({"4471": "RUNNING"})).reconcile_once() == 0
    )

    assert slack.messages == []
    row = await _get(engine, rid)
    assert row.state == "RUNNING" and row.notified_at is None


async def test_in_thread_when_the_researcher_chose_it(engine, session, alice):
    row = await link(session, alice, "U1")
    row.notify_in_thread = True
    session.add(row)
    await session.commit()
    await _record(session, alice)
    slack = FakeTransport()

    await _monitor(engine, slack, Poll({"4471": "FAILED"})).reconcile_once()

    (msg,) = slack.messages
    assert (msg.channel, msg.thread_ts) == ("C1", "10.0")
    assert "<@U1>" in msg.text and "FAILED" in msg.text


async def test_desktop_jobs_only_with_the_setting(engine, session, alice):
    await link(session, alice, "U1")
    await _record(session, alice, origin="app")
    slack = FakeTransport()
    poll = Poll({"4471": "COMPLETED"})

    await _monitor(engine, slack, poll).reconcile_once()
    assert slack.messages == [] and poll.calls == []

    config = await store.get_config(session)
    config.notify_desktop_jobs = True
    session.add(config)
    await session.commit()

    await _monitor(engine, slack, poll).reconcile_once()
    (msg,) = slack.messages
    assert msg.channel == "DU1", "desktop jobs always go by DM"


async def test_a_job_that_ended_while_vista_was_off_is_told_on_start(
    engine, session, alice
):
    """The state was recorded before shutdown, but the notice never went out."""
    await link(session, alice, "U1")
    await _record(
        session, alice, state="COMPLETED", finished_at="2026-10-16T11:00:00+00:00"
    )
    slack = FakeTransport()
    poll = Poll({})

    assert await _monitor(engine, slack, poll).reconcile_once() == 1

    assert poll.calls == [], "a known terminal state needs no poll"
    assert "1h 0m" in slack.messages[0].text


async def test_a_poll_failure_skips_the_job_until_next_time(engine, session, alice):
    await link(session, alice, "U1")
    rid = await _record(session, alice, "1")
    await _record(session, alice, "2")
    slack = FakeTransport()
    poll = Poll({"1": RuntimeError("no Lux login"), "2": "COMPLETED"})

    assert await _monitor(engine, slack, poll).reconcile_once() == 1

    assert "`2`" in slack.messages[0].text
    assert (await _get(engine, rid)).notified_at is None


async def test_a_job_with_no_slack_link_is_closed_quietly(engine, session, alice):
    rid = await _record(session, alice)
    slack = FakeTransport()

    assert (
        await _monitor(engine, slack, Poll({"4471": "COMPLETED"})).reconcile_once() == 0
    )

    assert slack.messages == []
    assert (await _get(engine, rid)).notified_at is not None


async def test_a_failed_post_is_retried_next_pass(engine, session, alice):
    await link(session, alice, "U1")
    rid = await _record(session, alice)
    slack = FakeTransport()
    slack.fail_next["post"] = "ratelimited"
    monitor = _monitor(engine, slack, Poll({"4471": "COMPLETED"}))

    assert await monitor.reconcile_once() == 0
    assert (await _get(engine, rid)).notified_at is None
    assert await monitor.reconcile_once() == 1


def test_elapsed():
    assert elapsed("2026-10-16T10:00:00+00:00", "2026-10-16T10:04:30+00:00") == "4m"
    assert elapsed("2026-10-16T10:00:00+00:00", "2026-10-17T12:00:00+00:00") == "1d 2h"
    assert elapsed("x", "y") == ""


async def test_the_live_poll_asks_as_the_requester(engine, session, alice, monkeypatch):
    from vista_backend.db.schemas import ChatSessionTable
    from vista_backend.slack import jobs

    from .conftest import project

    project_id = (await project(session, alice)).id
    chat = ChatSessionTable(
        project_id=project_id, user_id=alice.id, message_history=[], messages=[]
    )
    chat_id = chat.id
    session.add(chat)
    await session.commit()
    rid = await _record(session, alice, chat_session_id=chat_id)
    asked = []

    def builder(user, paths):
        async def invoke(name, args):
            asked.append((user.id, paths["output_dir"], name, args))
            return "JOBID=4471 STATE=COMPLETED"

        return invoke

    monkeypatch.setattr(jobs, "build_mcp_invoke", builder)

    record = await session.get(HpcJobRecordTable, rid)
    assert await jobs.poll_status(session, record) == "COMPLETED"
    ((user_id, out, name, args),) = asked
    assert user_id == alice.id and f"{project_id}-{alice.id}" in out
    assert (name, args) == (
        "get_hpc_job_status",
        {"job_id": "4471", "cluster": "frontier"},
    )
