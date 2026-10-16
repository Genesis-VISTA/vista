"""A chat run shown in Slack: one progress message, edited, then the answer."""

import asyncio
import json
import uuid

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.db.schemas import ChatSessionTable, SlackDetailTable
from vista_backend.services import chat_run
from vista_backend.services.chat_run import ChatRun
from vista_backend.slack import format
from vista_backend.slack.relay import WORKING, Place, RunRelay, step_label

from .conftest import project
from .fakes import FakeTransport

PLACE = Place(channel="DU1", thread_ts="10.0", slack_user_id="U1")


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def db(engine, monkeypatch):
    monkeypatch.setattr(chat_run, "session_factory", lambda: AsyncSession(engine))
    return engine


async def _chat(session, alice) -> uuid.UUID:
    p = await project(session, alice)
    row = ChatSessionTable(
        project_id=p.id,
        user_id=alice.id,
        message_history=[],
        messages=[],
        origin="slack",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row.id


def _run(chat_id: uuid.UUID, prompt="queue on Frontier?") -> ChatRun:
    return ChatRun(
        chat_session_id=chat_id,
        project_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        agent_key=("k",),  # type: ignore[arg-type]
        user_prompt=prompt,
        prior_history=[],
        channel="slack",
    )


async def _event(run: ChatRun, kind: str, **data) -> None:
    await run.append(kind, json.dumps({"event_kind": kind, **data}))


async def _tool(run: ChatRun, name: str) -> None:
    await _event(run, "function_tool_call", part={"tool_name": name, "args": {}})


async def _answer(run: ChatRun, text: str, state="done", warnings=()) -> None:
    await _event(
        run,
        "agent_run_result",
        result={
            "new_messages": [
                {"kind": "request", "parts": []},
                {"kind": "response", "parts": [{"part_kind": "text", "content": text}]},
            ],
            "egress_warnings": list(warnings),
        },
    )
    await _finish(run, state)


async def _finish(run: ChatRun, state: str) -> None:
    await _event(run, "run_finished", state=state)
    await run._finish()


async def _follow(relay: RunRelay, run: ChatRun, script) -> str:
    await relay.start()
    task = asyncio.create_task(relay.follow(run))
    await script()
    return await asyncio.wait_for(task, 5)


@pytest.mark.anyio
async def test_progress_then_the_answer_in_the_same_message(db, session, alice):
    slack = FakeTransport()
    clock = Clock()
    run = _run(await _chat(session, alice))
    relay = RunRelay(slack, PLACE, clock=clock)

    async def script():
        clock.t += 2
        await _tool(run, "vista__get_hpc_queue_status")
        await asyncio.sleep(0)
        await _answer(run, "Frontier's queue is short: **12 jobs** waiting.")

    answer = await _follow(relay, run, script)

    assert answer.startswith("Frontier's queue")
    (posted,) = slack.messages
    assert posted.thread_ts == "10.0"
    assert posted.edits[0] == f"{WORKING} _Checking the queue…_"
    assert posted.text == "Frontier's queue is short: *12 jobs* waiting."
    assert posted.blocks[0]["text"]["text"].startswith("Frontier's queue")


@pytest.mark.anyio
async def test_edits_are_throttled(db, session, alice):
    slack = FakeTransport()
    clock = Clock()
    run = _run(await _chat(session, alice))
    relay = RunRelay(slack, PLACE, clock=clock)

    async def script():
        for name in ["rag_search", "view", "run_bash"]:
            clock.t += 0.2  # quicker than progress_edit_interval
            await _tool(run, name)
            await asyncio.sleep(0)
        clock.t += 5
        await _tool(run, "create_file")
        await asyncio.sleep(0)
        await _answer(run, "Done.")

    await _follow(relay, run, script)

    edits = slack.messages[0].edits
    assert edits[:-1] == [f"{WORKING} _Writing a file…_"]


@pytest.mark.anyio
async def test_a_long_answer_keeps_the_rest_for_show_more(db, engine, session, alice):
    slack = FakeTransport()
    run = _run(await _chat(session, alice))
    long = "The short version is yes. " + " ".join(["More detail here."] * 600)

    await _follow(RunRelay(slack, PLACE), run, lambda: _answer(run, long))

    blocks = slack.messages[0].blocks
    button = blocks[-1]["elements"][0]
    assert button["action_id"] == format.SHOW_MORE
    async with AsyncSession(engine) as s:
        detail = await s.get(SlackDetailTable, button["value"])
    assert detail is not None and detail.slack_user_id == "U1"
    assert "More detail here." in detail.text


@pytest.mark.anyio
async def test_refused_blocks_fall_back_to_plain_text(db, session, alice):
    slack = FakeTransport()
    run = _run(await _chat(session, alice))
    relay = RunRelay(slack, PLACE)

    async def script():
        slack.fail_next["update"] = "invalid_blocks"
        await _answer(run, "Short answer.")

    await _follow(relay, run, script)

    (posted,) = slack.messages
    assert posted.text == "Short answer." and posted.blocks is None


@pytest.mark.anyio
async def test_a_failed_edit_posts_the_answer_instead(db, session, alice):
    slack = FakeTransport()
    run = _run(await _chat(session, alice))
    relay = RunRelay(slack, PLACE)

    async def script():
        slack.fail_next["update"] = "message_not_found"
        await _answer(run, "Short answer.")

    await _follow(relay, run, script)

    assert [m.text for m in slack.messages] == [WORKING, "Short answer."]
    assert slack.messages[1].thread_ts == "10.0"


@pytest.mark.anyio
async def test_a_failed_run_says_so(db, session, alice):
    slack = FakeTransport()
    run = _run(await _chat(session, alice))

    await _follow(RunRelay(slack, PLACE), run, lambda: _finish(run, "failed"))

    assert slack.messages[0].text.startswith("Sorry, that turn failed")


@pytest.mark.anyio
async def test_egress_warnings_are_shown_under_the_answer(db, session, alice):
    slack = FakeTransport()
    run = _run(await _chat(session, alice))
    warning = {"kind": "citation", "message": "One citation could not be verified."}

    await _follow(
        RunRelay(slack, PLACE),
        run,
        lambda: _answer(run, "LiF melts at 845 °C.", warnings=[warning]),
    )

    notes = [b for b in slack.messages[0].blocks if b["type"] == "context"]
    assert "could not be verified" in notes[0]["elements"][0]["text"]


@pytest.mark.anyio
async def test_prompts_go_to_the_handler(db, session, alice):
    slack = FakeTransport()
    run = _run(await _chat(session, alice))
    seen = []

    async def on_prompt(event):
        seen.append(event.prompt_id)

    async def script():
        await run.append(
            "mcp_tool_approval",
            json.dumps({"event_kind": "mcp_tool_approval", "elicitation_id": "e1"}),
            prompt_id="e1",
        )
        await asyncio.sleep(0)
        await _answer(run, "Submitted.")

    await _follow(RunRelay(slack, PLACE, on_prompt=on_prompt), run, script)

    assert seen == ["e1"]


@pytest.mark.anyio
async def test_the_turn_is_added_to_the_desktop_transcript_once(
    db, engine, session, alice
):
    slack = FakeTransport()
    chat_id = await _chat(session, alice)
    run = _run(chat_id)

    for _ in range(2):  # Following the same run twice must not duplicate it.
        if run.finished:
            await RunRelay(slack, PLACE).follow(run)
        else:
            await _follow(RunRelay(slack, PLACE), run, lambda: _answer(run, "Yes."))

    async with AsyncSession(engine) as s:
        row = await s.get(ChatSessionTable, chat_id)
        assert row is not None
        messages = row.messages
    assert [(m["role"], m["content"], m["run_id"]) for m in messages] == [
        ("user", "queue on Frontier?", run.run_id),
        ("assistant", "Yes.", run.run_id),
    ]


def test_step_labels():
    assert step_label("rag_search") == "Searching the literature"
    assert step_label("vista__submit_hpc_job") == "Submitting a job to the cluster"
    assert step_label("vista__something_new") == "Running `something_new`"
