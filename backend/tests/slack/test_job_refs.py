"""Jobs submitted from a chat are recorded, so "my last job" has an answer."""

import uuid

import pytest
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from harness import call, make_project, say, step_model
from vista_backend.agents.agents import ProjectAgent
from vista_backend.db.schemas import (
    ChatSessionTable,
    HpcJobRecordTable,
    SlackThreadTable,
)
from vista_backend.services import job_records
from vista_backend.services.job_records import SubmitWatcher
from vista_backend.slack import store

from .conftest import TEAM, project

pytestmark = pytest.mark.anyio

ARGS = {
    "job": "flibe-md",
    "cluster": "frontier",
    "node_count": 2,
    "duration": "1:00:00",
    "script_args": "--steps 1000",
}
SUMMARY = (
    "job_id: 4471\ncluster: frontier\nlog_path: /lustre/x/4471.out\n"
    "err_path: /lustre/x/4471.err\noutput_dir: /lustre/x"
)


async def _chat(session, alice) -> uuid.UUID:
    p = await project(session, alice)
    row = ChatSessionTable(
        project_id=p.id, user_id=alice.id, message_history=[], messages=[]
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row.id


def _watcher(engine, alice, chat_id, channel="app") -> SubmitWatcher:
    return SubmitWatcher(
        session_factory=lambda: AsyncSession(engine),
        user_id=alice.id,
        chat_session_id=chat_id,
        channel=channel,
    )


async def _submit(
    watcher, summary=SUMMARY, *, tool="vista__submit_hpc_job", fail=False
):
    await watcher.observe(
        FunctionToolCallEvent(
            part=ToolCallPart(tool_name=tool, args=ARGS, tool_call_id="c1")
        )
    )
    if fail:
        part = RetryPromptPart(content="boom", tool_name=tool, tool_call_id="c1")
    else:
        part = ToolReturnPart(tool_name=tool, content=summary, tool_call_id="c1")
    await watcher.observe(FunctionToolResultEvent(part=part))


async def _records(engine) -> list[HpcJobRecordTable]:
    async with AsyncSession(engine) as s:
        return list((await s.exec(select(HpcJobRecordTable))).all())


async def test_a_desktop_submit_is_recorded(engine, session, alice):
    chat_id = await _chat(session, alice)

    await _submit(_watcher(engine, alice, chat_id))

    (job,) = await _records(engine)
    assert (job.cluster, job.job_id, job.job) == ("frontier", "4471", "flibe-md")
    assert (job.node_count, job.duration, job.script_args) == (
        2,
        "1:00:00",
        "--steps 1000",
    )
    assert job.origin == "app" and job.slack_thread_ts is None
    assert job.user_id == alice.id and job.chat_session_id == chat_id


async def test_a_slack_submit_remembers_its_thread(engine, session, alice):
    chat_id = await _chat(session, alice)
    session.add(
        SlackThreadTable(
            team_id=TEAM,
            channel_id="DU1",
            thread_ts="10.0",
            chat_session_id=chat_id,
            slack_user_id="U1",
            created_at=store.now_iso(),
        )
    )
    await session.commit()

    await _submit(_watcher(engine, alice, chat_id, channel="slack"))

    (job,) = await _records(engine)
    assert job.origin == "slack"
    assert (job.slack_team_id, job.slack_channel_id, job.slack_thread_ts) == (
        TEAM,
        "DU1",
        "10.0",
    )
    assert job.slack_user_id == "U1"


async def test_a_failed_or_refused_submit_records_nothing(engine, session, alice):
    chat_id = await _chat(session, alice)
    watcher = _watcher(engine, alice, chat_id)

    await _submit(watcher, fail=True)
    await _submit(watcher, "The researcher declined this call.")

    assert await _records(engine) == []


async def test_other_tools_are_not_recorded(engine, session, alice):
    chat_id = await _chat(session, alice)

    await _submit(_watcher(engine, alice, chat_id), tool="vista__get_hpc_job_status")

    assert await _records(engine) == []


async def test_the_same_job_twice_is_one_record(engine, session, alice):
    chat_id = await _chat(session, alice)
    watcher = _watcher(engine, alice, chat_id)

    await _submit(watcher)
    await _submit(watcher)

    assert len(await _records(engine)) == 1


async def test_my_recent_jobs_are_newest_first_and_mine_only(session, alice, bob):
    other = bob.id
    for i, (user, cluster) in enumerate(
        [(alice.id, "lux"), (alice.id, "frontier"), (other, "frontier")]
    ):
        session.add(
            HpcJobRecordTable(
                user_id=user,
                cluster=cluster,
                job_id=str(100 + i),
                job="flibe-md",
                submitted_at=f"2026-10-16T0{i}:00:00+00:00",
            )
        )
    await session.commit()

    jobs = await job_records.recent_jobs(session, alice.id)
    assert [j.job_id for j in jobs] == ["101", "100"]
    lux = await job_records.recent_jobs(session, alice.id, cluster="lux")
    assert [j.job_id for j in lux] == ["100"]
    assert "job_id: 101; cluster: frontier" in job_records.describe(jobs)
    assert "No jobs are recorded" in job_records.describe([])


async def test_the_agent_can_list_my_recent_jobs(engine, session, alice, monkeypatch):
    user = alice
    session.add(
        HpcJobRecordTable(
            user_id=user.id,
            cluster="frontier",
            job_id="4471",
            job="flibe-md",
            submitted_at=store.now_iso(),
        )
    )
    await session.commit()
    monkeypatch.setattr(
        "vista_backend.agents.agents.get_engine", lambda: engine, raising=True
    )
    agent = ProjectAgent(make_project(tools=["*"]), user, uuid.uuid4())
    results = []
    model = step_model([call("list_my_recent_jobs"), say("done")])
    with agent.agent.override(model=model):
        async for event in agent.run_stream(
            user_prompt="my last job?", channel="slack"
        ):
            if isinstance(event, FunctionToolResultEvent):
                results.append(str(event.part.content))

    assert any("job_id: 4471" in r for r in results)
