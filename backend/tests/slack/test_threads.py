"""One Slack thread is one VISTA conversation."""

import uuid

import pytest
from sqlmodel import select

from vista_backend.db.schemas import ChatSessionTable
from vista_backend.slack.events import Message
from vista_backend.slack.threads import (
    ChooseProject,
    NoProject,
    NotYourThread,
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
async def test_a_top_level_message_starts_a_slack_conversation(session):
    p = await project(session)

    convo = await conversation_for(session, msg(), project_id=None, user_id="local")

    assert convo.is_new and convo.project.id == p.id
    chat = await session.get(ChatSessionTable, convo.chat_session_id)
    assert chat.origin == "slack"
    assert chat.title == "queue on Frontier?"


@pytest.mark.anyio
async def test_a_reply_in_the_thread_continues_it(session):
    await project(session)
    first = await conversation_for(
        session, msg(ts="10.0"), project_id=None, user_id="local"
    )

    again = await conversation_for(
        session,
        msg(text="and Lux?", ts="11.0", thread_ts="10.0"),
        project_id=None,
        user_id="local",
    )
    other = await conversation_for(
        session, msg(ts="12.0"), project_id=None, user_id="local"
    )

    assert again.chat_session_id == first.chat_session_id and not again.is_new
    assert other.chat_session_id != first.chat_session_id


@pytest.mark.anyio
async def test_the_same_ts_in_another_channel_is_another_thread(session):
    await project(session)
    a = await conversation_for(
        session, msg(channel="C1"), project_id=None, user_id="local"
    )
    b = await conversation_for(
        session, msg(channel="C2"), project_id=None, user_id="local"
    )
    assert a.chat_session_id != b.chat_session_id


@pytest.mark.anyio
async def test_someone_elses_thread_is_refused(session):
    """Two linked Slack accounts: one may not continue the other's thread."""
    await project(session)
    await conversation_for(session, msg(channel="C1"), project_id=None, user_id="local")

    with pytest.raises(NotYourThread):
        await conversation_for(
            session,
            msg(channel="C1", ts="11.0", thread_ts="10.0", user="U2"),
            project_id=None,
            user_id="local",
        )


@pytest.mark.anyio
async def test_the_chosen_project_is_used_while_it_exists(session):
    await project(session, "a-first")
    chosen = (await project(session, "b-chosen")).id
    deleted = uuid.uuid4()

    assert (
        await conversation_for(session, msg(ts="1"), project_id=chosen, user_id="local")
    ).project.id == chosen
    with pytest.raises(ChooseProject) as asked:
        await conversation_for(
            session, msg(ts="2"), project_id=deleted, user_id="local"
        )
    assert [name for _, name in asked.value.choices] == ["a-first", "b-chosen"]


@pytest.mark.anyio
async def test_several_projects_and_none_chosen_asks(session):
    await project(session, "molten-salts")
    await project(session, "Frontier-runs")

    with pytest.raises(ChooseProject) as asked:
        await conversation_for(session, msg(), project_id=None, user_id="local")

    assert [name for _, name in asked.value.choices] == [
        "Frontier-runs",
        "molten-salts",
    ]
    assert (await session.exec(select(ChatSessionTable))).all() == [], "nothing started"


@pytest.mark.anyio
async def test_an_only_project_is_used_without_asking(session):
    only = await project(session)
    convo = await conversation_for(session, msg(), project_id=None, user_id="local")
    assert convo.project.id == only.id


@pytest.mark.anyio
async def test_no_project_says_so(session):
    with pytest.raises(NoProject):
        await conversation_for(session, msg(), project_id=None, user_id="local")


@pytest.mark.anyio
async def test_a_conversation_deleted_on_the_desktop_starts_again(session):
    await project(session)
    first = await conversation_for(session, msg(), project_id=None, user_id="local")
    await session.delete(await session.get(ChatSessionTable, first.chat_session_id))
    await session.commit()

    again = await conversation_for(
        session, msg(ts="11.0", thread_ts="10.0"), project_id=None, user_id="local"
    )
    assert again.is_new and again.chat_session_id != first.chat_session_id
