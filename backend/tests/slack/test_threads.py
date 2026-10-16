"""One Slack thread is one VISTA conversation, and its turns run in order."""

import asyncio
import uuid

import pytest

from vista_backend.db.schemas import ChatSessionTable
from vista_backend.slack.events import Message
from vista_backend.slack.threads import (
    MAX_WAITING,
    NoProject,
    NotYourThread,
    ThreadQueue,
    conversation_for,
)

from .conftest import TEAM, project


def msg(text="queue on Frontier?", ts="10.0", thread_ts=None, user="U1", channel="DU1"):
    return Message(
        team_id=TEAM,
        channel_id=channel,
        user_id=user,
        text=text,
        ts=ts,
        thread_ts=thread_ts,
        is_dm=True,
    )


@pytest.mark.anyio
async def test_a_top_level_message_starts_a_slack_conversation(session, alice):
    p = await project(session, alice)
    user = alice

    convo = await conversation_for(session, msg(), user=user, project_id=None)

    assert convo.is_new and convo.project.id == p.id
    chat = await session.get(ChatSessionTable, convo.chat_session_id)
    assert chat.origin == "slack"
    assert chat.title == "queue on Frontier?"


@pytest.mark.anyio
async def test_a_reply_in_the_thread_continues_it(session, alice):
    await project(session, alice)
    user = alice
    first = await conversation_for(session, msg(ts="10.0"), user=user, project_id=None)

    again = await conversation_for(
        session,
        msg(text="and Lux?", ts="11.0", thread_ts="10.0"),
        user=user,
        project_id=None,
    )
    other = await conversation_for(session, msg(ts="12.0"), user=user, project_id=None)

    assert again.chat_session_id == first.chat_session_id and not again.is_new
    assert other.chat_session_id != first.chat_session_id


@pytest.mark.anyio
async def test_the_same_ts_in_another_channel_is_another_thread(session, alice):
    await project(session, alice)
    user = alice
    a = await conversation_for(session, msg(channel="C1"), user=user, project_id=None)
    b = await conversation_for(session, msg(channel="C2"), user=user, project_id=None)
    assert a.chat_session_id != b.chat_session_id


@pytest.mark.anyio
async def test_someone_elses_thread_is_refused(session, alice, bob):
    await project(session, alice)
    await project(session, bob, "bobs")
    a = alice
    b = bob
    await conversation_for(session, msg(channel="C1"), user=a, project_id=None)

    with pytest.raises(NotYourThread):
        await conversation_for(
            session,
            msg(channel="C1", ts="11.0", thread_ts="10.0", user="U2"),
            user=b,
            project_id=None,
        )


@pytest.mark.anyio
async def test_the_chosen_project_is_used_when_still_allowed(session, alice, bob):
    await project(session, alice, "a-first")
    chosen = (await project(session, alice, "b-chosen")).id
    foreign = (await project(session, bob, "c-bobs")).id
    user = alice

    assert (
        await conversation_for(session, msg(ts="1"), user=user, project_id=chosen)
    ).project.id == chosen
    fallback = await conversation_for(
        session, msg(ts="2"), user=user, project_id=foreign
    )
    assert fallback.project.name == "a-first"


@pytest.mark.anyio
async def test_no_project_says_so(session, alice):
    with pytest.raises(NoProject):
        await conversation_for(session, msg(), user=alice, project_id=None)


@pytest.mark.anyio
async def test_a_conversation_deleted_on_the_desktop_starts_again(session, alice):
    await project(session, alice)
    user = alice
    first = await conversation_for(session, msg(), user=user, project_id=None)
    await session.delete(await session.get(ChatSessionTable, first.chat_session_id))
    await session.commit()

    again = await conversation_for(
        session, msg(ts="11.0", thread_ts="10.0"), user=user, project_id=None
    )
    assert again.is_new and again.chat_session_id != first.chat_session_id


@pytest.mark.anyio
async def test_turns_in_a_thread_run_in_order_and_are_not_dropped():
    queue = ThreadQueue()
    key = uuid.uuid4()
    order: list[str] = []
    release = asyncio.Event()

    async def first():
        order.append("first start")
        await release.wait()
        order.append("first end")

    async def second():
        order.append("second")

    t1 = asyncio.create_task(queue.run(key, first))
    await asyncio.sleep(0)
    t2 = asyncio.create_task(queue.run(key, second))
    await asyncio.sleep(0)
    assert queue.busy(key) and order == ["first start"]

    release.set()
    assert await t1 and await t2
    assert order == ["first start", "first end", "second"]
    assert not queue.busy(key)


@pytest.mark.anyio
async def test_too_many_waiting_is_refused_not_queued():
    queue = ThreadQueue()
    key = uuid.uuid4()
    release = asyncio.Event()

    async def hold():
        await release.wait()

    running = asyncio.create_task(queue.run(key, hold))
    await asyncio.sleep(0)
    waiting = [asyncio.create_task(queue.run(key, hold)) for _ in range(MAX_WAITING)]
    await asyncio.sleep(0)

    assert await queue.run(key, hold) is False
    release.set()
    assert all(await asyncio.gather(running, *waiting))


@pytest.mark.anyio
async def test_other_threads_do_not_wait():
    queue = ThreadQueue()
    release = asyncio.Event()

    async def hold():
        await release.wait()

    ran = []

    async def quick():
        ran.append(True)

    blocked = asyncio.create_task(queue.run(uuid.uuid4(), hold))
    await asyncio.sleep(0)
    assert await queue.run(uuid.uuid4(), quick) and ran == [True]
    release.set()
    await blocked
