"""
The conversation inbox (`services/inbox.py`, openspec change background-tasks,
tasks 2.2 and 2.5): arrival order, changing an item until it is taken, and the
rule that holds back turns nobody asked for.

The hold-back cases run real turns through the chat run registry, on the
scripted conversations of `test_chat_run.py`.
"""

import uuid

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from harness import say, seed_project
from test_chat_run import Env, env  # noqa: F401  (the registry fixture)
from vista_backend.db.schemas import ChatSessionTable
from vista_backend.services import chat_run
from vista_backend.services import chat_session as chat_session_service
from vista_backend.services import inbox

pytestmark = [pytest.mark.anyio]


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


@pytest.fixture
def quiet_inbox(monkeypatch):
    """No registry listens: a put only queues."""
    monkeypatch.setattr(inbox, "_listeners", [])


async def _chat(session) -> uuid.UUID:
    project = await seed_project(session)
    row = await chat_session_service.create_chat_session(session, project_id=project.id)
    row_id = row.id
    await session.commit()
    return row_id


@pytest.mark.unit
async def test_items_are_taken_in_arrival_order_and_only_once(session, quiet_inbox):
    chat = await _chat(session)
    for text in ("first", "second", "third"):
        await inbox.put(session, chat, kind="task_event", text=text)

    taken = await inbox.take_queued(session, chat)

    assert [i.text for i in taken] == ["first", "second", "third"]
    assert {i.state for i in taken} == {"taken"}
    assert all(i.taken_at for i in taken)
    assert await inbox.take_queued(session, chat) == []


@pytest.mark.unit
async def test_an_edit_before_the_take_is_what_is_taken(session, quiet_inbox):
    chat = await _chat(session)
    item = await inbox.put(session, chat, kind="message", text="plot it")

    edited = await inbox.edit(session, chat, item.id, "plot it in log scale")

    assert edited.text == "plot it in log scale"
    assert [i.text for i in await inbox.take_queued(session, chat)] == [
        "plot it in log scale"
    ]


@pytest.mark.unit
async def test_a_withdrawn_item_is_never_taken(session, quiet_inbox):
    chat = await _chat(session)
    gone = await inbox.put(session, chat, kind="message", text="never mind")
    await inbox.put(session, chat, kind="message", text="keep this")

    assert (await inbox.withdraw(session, chat, gone.id)).state == "withdrawn"

    assert [i.text for i in await inbox.take_queued(session, chat)] == ["keep this"]


@pytest.mark.unit
async def test_edit_and_withdraw_are_refused_once_taken(session, quiet_inbox):
    chat = await _chat(session)
    item = await inbox.put(session, chat, kind="message", text="plot it")
    await inbox.take_queued(session, chat)

    with pytest.raises(inbox.ItemTaken) as refused:
        await inbox.edit(session, chat, item.id, "changed my mind")
    assert refused.value.item.state == "taken"
    assert refused.value.item.text == "plot it", "the delivered text is unchanged"
    with pytest.raises(inbox.ItemTaken):
        await inbox.withdraw(session, chat, item.id)


@pytest.mark.unit
async def test_another_conversations_item_is_not_found(session, quiet_inbox):
    mine, theirs = await _chat(session), await _chat(session)
    item = await inbox.put(session, theirs, kind="message", text="theirs")

    with pytest.raises(inbox.ItemNotFound):
        await inbox.edit(session, mine, item.id, "mine now")
    with pytest.raises(inbox.ItemNotFound):
        await inbox.withdraw(session, mine, 999_999)


@pytest.mark.unit
async def test_a_put_tells_the_listeners_after_it_commits(engine, session, monkeypatch):
    chat = await _chat(session)
    seen: list[tuple[uuid.UUID, list[str]]] = []

    async def listener(chat_session_id):
        async with AsyncSession(engine) as other:  # sees only what is committed
            queued = await inbox.list_queued(other, chat_session_id)
        seen.append((chat_session_id, [i.text for i in queued]))

    monkeypatch.setattr(inbox, "_listeners", [])
    remove = inbox.on_put(listener)
    await inbox.put(session, chat, kind="task_event", text="done")
    remove()
    await inbox.put(session, chat, kind="task_event", text="unheard")

    assert seen == [(chat, ["done"])]


@pytest.mark.unit
async def test_only_queued_task_results_wait_on_the_researcher(session, quiet_inbox):
    result, message, taken = (
        await _chat(session),
        await _chat(session),
        await _chat(session),
    )
    await inbox.put(session, result, kind="task_event", text="done")
    await inbox.put(session, message, kind="message", text="hi")
    await inbox.put(session, taken, kind="task_event", text="done")
    await inbox.take_queued(session, taken)

    assert await inbox.waiting_on_researcher(session, [result, message, taken]) == {
        result
    }


# ---------------------------------------------------------------------------
# Holding back turns nobody asked for
# ---------------------------------------------------------------------------


async def _put_and_finish(on: Env, row, **item):
    await inbox.put(on.session, row.id, **item)
    run = on.registry.get(row.id)
    if run is not None:
        await run.wait()
    return run


async def test_the_eleventh_result_in_a_row_waits_for_the_researcher(env):  # noqa: F811
    replies = [say(f"reply {n}") for n in range(1, 12)]
    row, _ = await env.conversation(replies)

    for n in range(1, 11):
        run = await _put_and_finish(env, row, kind="task_event", text=f"result {n}")
        assert run is not None and run.origin == "task", f"result {n} starts a turn"
    assert (await env.row(row.id)).auto_turns == 10

    assert await _put_and_finish(env, row, kind="task_event", text="result 11") is None
    async with AsyncSession(env.engine) as db:
        waiting = await inbox.waiting_on_researcher(db, [row.id])
    assert chat_run.row_status(await env.row(row.id), waiting=row.id in waiting) == (
        "needs_you"
    )

    run = await _put_and_finish(env, row, kind="message", text="what did 11 say?")
    assert run is not None and run.origin == "researcher"
    assert run.user_prompt == "result 11\n\nwhat did 11 say?", "the result comes first"
    assert (await env.row(row.id)).auto_turns == 0

    later = await _put_and_finish(env, row, kind="task_event", text="result 12")
    assert later is not None, "results start turns again"


async def test_a_note_that_does_not_wake_waits_for_the_next_message(env):  # noqa: F811
    row, _ = await env.conversation([say("noted")])

    assert (
        await _put_and_finish(
            env, row, kind="task_event", text="bg-1 was interrupted", wakes=False
        )
        is None
    )

    run = await _put_and_finish(env, row, kind="message", text="carry on")
    assert run is not None
    assert run.user_prompt == "bg-1 was interrupted\n\ncarry on"


async def test_a_message_starts_a_turn_in_a_quiet_conversation(env):  # noqa: F811
    row, _ = await env.conversation([say("hello again")])
    async with AsyncSession(env.engine) as db:
        saved = await db.get(ChatSessionTable, row.id)
        saved.quiet = True
        saved.auto_turns = 10
        await db.commit()

    run = await _put_and_finish(env, row, kind="message", text="hello")

    assert run is not None
    saved = await env.row(row.id)
    assert saved.quiet is False and saved.auto_turns == 0


async def test_a_turn_that_could_not_start_leaves_its_items_queued(env, monkeypatch):  # noqa: F811
    row, _ = await env.conversation([])

    async def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(env.registry, "start", broken)
    monkeypatch.setattr(inbox, "_listeners", [])
    await inbox.put(env.session, row.id, kind="message", text="hello")

    with pytest.raises(RuntimeError):
        await env.registry.wake(row.id)
    async with AsyncSession(env.engine) as db:
        assert [i.text for i in await inbox.list_queued(db, row.id)] == ["hello"]
