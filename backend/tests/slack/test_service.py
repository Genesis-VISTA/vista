"""The Slack service: nothing starts while Slack is off; on, events reach the agent."""

# `env` is test_chat_run's fixture, imported, so each test that takes it "redefines" it.
# ruff: noqa: F811

import asyncio
import json

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from harness import call, say
from test_chat_run import env  # noqa: F401  (scripted agents and a registry)
from vista_backend.db.schemas import (
    ChatSessionTable,
    SlackAuditTable,
    SlackConfigTable,
    SlackProjectAskTable,
    SlackThreadTable,
)
from vista_backend.services import chat_run, inbox
from vista_backend.slack import confirm, gate, limits, service, store
from vista_backend.slack.events import Action, HomeOpened, Message
from vista_backend.slack.service import SlackService
from vista_backend.utils import secret_store

from .conftest import TEAM, link, project
from .fakes import FakeTransport

pytestmark = pytest.mark.anyio

BOT = "xoxb-1111-2222-notreal"
APP = "xapp-1-A111-2222-notreal"


@pytest.fixture
def db(engine, monkeypatch):
    monkeypatch.setattr(chat_run, "session_factory", lambda: AsyncSession(engine))
    return engine


@pytest.fixture
def slack(monkeypatch):
    fake = FakeTransport(team_id=TEAM)
    made: list[tuple[str, str]] = []

    def factory(bot, app):
        made.append((bot, app))
        return fake

    monkeypatch.setattr(service, "default_transport", factory)
    fake.made = made  # type: ignore[attr-defined]
    return fake


@pytest.fixture
async def on(db, session, slack_config):
    secret_store.put(secret_store.SLACK_BOT_TOKEN, BOT)
    secret_store.put(secret_store.SLACK_APP_TOKEN, APP)
    return slack_config


def _service() -> SlackService:
    return SlackService(host="lab-mac")


def _dm(text: str, ts="10.0", user="U1", thread_ts=None) -> Message:
    return Message(
        team_id=TEAM,
        channel_id=f"D{user}",
        user_id=user,
        text=text,
        ts=ts,
        thread_ts=thread_ts,
        is_dm=True,
    )


async def test_nothing_starts_while_slack_is_off(db, slack):
    svc = _service()

    assert await svc.start() is False

    assert slack.made == [], "no transport was even built"  # type: ignore[attr-defined]
    assert svc.state == "off" and svc.detail == "Slack is off."
    assert svc._tasks == []
    assert gate._refusal is gate.no_refusal
    assert confirm._preview is confirm.no_preview


async def test_no_tokens_nothing_starts(db, slack, slack_config):
    svc = _service()
    assert await svc.start() is False
    assert "token" in (svc.detail or "")
    assert slack.made == []  # type: ignore[attr-defined]


async def test_lifespan_module_start_returns_none_when_off(db, slack):
    assert await service.start() is None
    assert service.status() == ("off", "Slack is off.")
    await service.stop()


async def test_on_it_connects_and_installs_the_checks(on, slack, session):
    await link(session, "U1")
    svc = _service()

    assert await svc.start() is True

    assert slack.made == [(BOT, APP)]  # type: ignore[attr-defined]
    assert slack.connected and svc.state == "connected"
    assert gate._refusal is limits.refusal
    assert confirm._preview is service.preview_job
    assert "U1" in slack.homes, "the Home tab is published on connect"

    await svc.stop()
    assert not slack.connected and svc.state == "off"
    assert gate._refusal is gate.no_refusal
    assert svc._tasks == []


async def test_connect_records_when_for_the_home_tab(on, slack, session):
    svc = _service()
    await svc.start()

    async with AsyncSession(session.bind) as s:
        config = await s.get(SlackConfigTable, 1)
        assert config is not None and config.online_since is not None
    await svc.stop()


async def test_stop_marks_the_home_offline_and_sends_no_dm(on, slack, session):
    await link(session, "U1")
    svc = _service()
    await svc.start()

    await svc.stop()

    assert "Offline" in json.dumps(slack.homes["U1"])
    assert slack.in_channel("DU1") == [], "no going-offline notice"


async def test_a_failed_connect_is_reported_and_retried(on, slack, monkeypatch):
    from vista_backend.config import settings

    monkeypatch.setattr(settings.slack, "reconnect_interval", 0.01)
    slack.fail_next["connect"] = "invalid_auth"
    svc = _service()

    assert await svc.start() is True
    assert svc.state == "error" and "invalid_auth" in (svc.detail or "")

    for _ in range(100):
        if slack.connected:
            break
        await asyncio.sleep(0.01)
    assert slack.connected and svc.state == "connected"
    await svc.stop()


async def test_a_dropped_connection_is_connected_again(on, slack, monkeypatch):
    from vista_backend.config import settings

    monkeypatch.setattr(settings.slack, "reconnect_interval", 0.01)
    svc = _service()
    await svc.start()

    await slack.disconnect()
    for _ in range(100):
        if slack.connected:
            break
        await asyncio.sleep(0.01)
    assert slack.connected and slack.connects == 2
    await svc.stop()


async def _slack_conversation(on, steps, *, thread_ts="10.0", user="U1"):
    """A Slack conversation on a scripted agent, bound to a DM thread."""
    row, _ = await on.conversation(steps)
    async with AsyncSession(on.engine) as s:
        saved = await s.get(ChatSessionTable, row.id)
        saved.origin = "slack"
        s.add(
            SlackThreadTable(
                team_id=TEAM,
                channel_id=f"D{user}",
                thread_ts=thread_ts,
                chat_session_id=row.id,
                slack_user_id=user,
                created_at=store.now_iso(),
            )
        )
        await s.commit()
    return row


async def _settled(on, chat_id) -> None:
    """Until no turn runs and nothing is queued, and the relay is done."""
    async with asyncio.timeout(10):
        while True:
            run = on.registry.get(chat_id)
            if run is not None:
                await run.wait()
                continue
            async with AsyncSession(on.engine) as db:
                if not await inbox.list_queued(db, chat_id):
                    break
            await asyncio.sleep(0.01)
        for task in list(service.following.values()):
            await task


def _answers(slack, channel="DU1", thread="10.0") -> list[str]:
    return [
        (m.edits or [m.text])[-1]
        for m in slack.in_channel(channel)
        if m.thread_ts == thread
    ]


@pytest.fixture
def credential(monkeypatch):
    """The install has a model to send to."""
    monkeypatch.setattr(service, "require_inference_credential", lambda *a: None)


async def test_a_dm_runs_a_slack_turn(env, on, slack, session, credential):
    await link(session, "U1")
    row = await _slack_conversation(env, [say("Queue is short.")])
    await service.start(host="lab-mac")

    await slack.deliver(_dm("<@UBOT> how is the Frontier queue?"))
    await _settled(env, row.id)

    saved = await env.row(row.id)
    assert saved.run_state == "done"
    assert any("Queue is short." in a for a in _answers(slack))
    assert [(m["role"], m["content"]) for m in saved.messages] == [
        ("user", "how is the Frontier queue?"),
        ("assistant", "Queue is short."),
    ]
    assert slack.reactions.get(("DU1", "10.0"), []) == [], "nothing waited"
    async with AsyncSession(session.bind) as s:
        events = [r.event for r in (await s.exec(select(SlackAuditTable))).all()]
    assert "request" in events


async def test_messages_during_a_turn_are_all_answered_in_order(
    env,
    on,
    slack,
    session,
    credential,
):
    await link(session, "U1")
    row = await _slack_conversation(env, [call("gate"), say("Done with all three.")])
    await service.start(host="lab-mac")

    await slack.deliver(_dm("start the long one"))
    await asyncio.wait_for(env.at_gate.wait(), 10)
    for n, ts in enumerate(["11.0", "12.0", "13.0"], 1):
        await slack.deliver(_dm(f"also {n}", ts=ts, thread_ts="10.0"))
    assert all(
        slack.reactions[("DU1", ts)] == [service.WAITING]
        for ts in ["11.0", "12.0", "13.0"]
    ), "each waiting message is marked received"

    env.gate.set()
    await _settled(env, row.id)

    saved = await env.row(row.id)
    prompts = [
        part["content"]
        for message in saved.message_history
        if message["kind"] == "request"
        for part in message["parts"]
        if part["part_kind"] == "user-prompt"
    ]
    assert [p.splitlines()[-1] for p in prompts] == [
        "start the long one",
        "also 1",
        "also 2",
        "also 3",
    ]
    assert all(
        slack.reactions[("DU1", ts)] == [service.TAKEN]
        for ts in ["11.0", "12.0", "13.0"]
    )
    assert [m["content"] for m in saved.messages if m["role"] == "user"] == [
        "start the long one",
        "also 1",
        "also 2",
        "also 3",
    ]
    assert any("Done with all three." in a for a in _answers(slack))


async def test_a_message_waiting_across_a_restart_is_answered(
    env,
    on,
    slack,
    session,
    credential,
):
    await link(session, "U1")
    row = await _slack_conversation(env, [say("Here it is.")])
    async with AsyncSession(env.engine) as db:
        saved = await db.get(ChatSessionTable, row.id)
        saved.run_state = "running"  # the turn the quit cut short
        await db.commit()
        await inbox.put(
            db,
            row.id,
            kind="message",
            text="and the outputs?",
            payload={"slack": {"channel": "DU1", "ts": "11.0"}},
            notify=False,
        )

    # What the lifespan does at startup, in its order.
    assert await chat_run.sweep_interrupted_runs() == 1
    assert await chat_run.wake_queued_inboxes() == 1
    await service.start(host="lab-mac")
    await _settled(env, row.id)

    assert any("Here it is." in a for a in _answers(slack))
    saved = await env.row(row.id)
    assert [m["content"] for m in saved.messages if m["role"] == "user"] == [
        "and the outputs?"
    ]


async def test_a_turn_vista_starts_is_shown_in_the_thread_once(
    env,
    on,
    slack,
    session,
):
    await link(session, "U1")
    row = await _slack_conversation(env, [say("Job 42 finished; outputs are in.")])
    await service.start(host="lab-mac")

    async with AsyncSession(env.engine) as db:
        await inbox.put(db, row.id, kind="task_event", text="[watch] job 42 COMPLETED")
    await _settled(env, row.id)

    run_rows = (await env.row(row.id)).messages
    assert [(m["role"], m["content"]) for m in run_rows] == [
        ("assistant", "Job 42 finished; outputs are in.")
    ], "no user bubble for a task result, and the answer once"
    (progress,) = [m for m in slack.in_channel("DU1") if m.thread_ts == "10.0"]
    assert "Job 42 finished" in progress.edits[-1]


async def test_a_turn_running_at_quit_says_so_in_its_thread(
    env,
    on,
    slack,
    session,
    credential,
):
    await link(session, "U1")
    row = await _slack_conversation(env, [call("hang"), say("unreachable")])
    await service.start(host="lab-mac")
    await slack.deliver(_dm("start the long one"))
    await asyncio.wait_for(env.hang_started.wait(), 10)

    # The lifespan's order: Slack first, then the runs.
    await service.stop()
    await env.registry.stop_all(reason="interrupted")
    for task in list(service.following.values()):
        await task

    assert (await env.row(row.id)).run_state == "interrupted"
    assert any("VISTA quit before that turn finished" in a for a in _answers(slack))


async def test_a_turn_in_an_app_conversation_is_not_followed(
    env,
    on,
    slack,
    session,
):
    row, _ = await env.conversation([say("Done.")])
    await service.start(host="lab-mac")

    async with AsyncSession(env.engine) as db:
        await inbox.put(db, row.id, kind="task_event", text="job 42 COMPLETED")
    await _settled(env, row.id)

    assert slack.messages == []


class _NewChats(dict):
    """
    `env.agents` for conversations the service creates: each borrows one agent
    scripted with `steps`, made beforehand (an override must be entered in the
    test's own context).
    """

    def __init__(self, agents, agent) -> None:
        super().__init__(agents)
        self.agent = agent

    def __missing__(self, chat_id):
        return self.agent


async def _for_new_chats(env, steps) -> None:
    row, _ = await env.conversation(steps)  # an app chat, only to hold the agent
    env.agents = _NewChats(env.agents, env.agents[row.id])


def _picker(slack, thread="10.0"):
    (asked,) = [
        m
        for m in slack.in_channel("DU1")
        if m.thread_ts == thread and m.text == service.WHICH_PROJECT
    ]
    elements = asked.blocks[-1]["elements"]
    return asked, {e["text"]["text"]: e for e in elements}


def _tap(asked, button, user="U1") -> Action:
    return Action(
        team_id=TEAM,
        channel_id="DU1",
        user_id=user,
        action_id=button["action_id"],
        value=button["value"],
        message_ts=asked.ts,
        thread_ts=asked.thread_ts,
    )


async def _slack_chats(env) -> list[ChatSessionTable]:
    async with AsyncSession(env.engine) as db:
        rows = (await db.exec(select(ChatSessionTable))).all()
        return [r for r in rows if r.origin == "slack"]


async def test_with_several_projects_vista_asks_and_answers_in_the_one_tapped(
    env, on, slack, session, credential
):
    await link(session, "U1")
    await link(session, "U2")
    other = await project(session, "zz-frontier-runs")
    other_id = other.id
    await _for_new_chats(env, [say("Answered in the project you picked.")])
    await service.start(host="lab-mac")

    await slack.deliver(_dm("how is the Frontier queue?"))
    asked, buttons = _picker(slack)
    assert set(buttons) == {env.project.name, "zz-frontier-runs"}
    assert await _slack_chats(env) == [], "nothing starts before the tap"

    await slack.deliver(_dm("and Lux?", ts="11.0", thread_ts="10.0"))
    assert slack.reactions[("DU1", "11.0")] == [service.WAITING], "a reply waits"

    await slack.deliver(_tap(asked, buttons["zz-frontier-runs"], user="U2"))
    assert any(
        "can choose" in text for _, user, text in slack.ephemerals if user == "U2"
    )
    assert await _slack_chats(env) == [], "another user's tap counts for nothing"

    await slack.deliver(_tap(asked, buttons["zz-frontier-runs"]))
    (chat,) = await _slack_chats(env)
    await _settled(env, chat.id)

    saved = await env.row(chat.id)
    assert saved.project_id == other_id
    assert [m["content"] for m in saved.messages if m["role"] == "user"] == [
        "how is the Frontier queue?",
        "and Lux?",
    ]
    assert slack.reactions[("DU1", "11.0")] == [service.TAKEN]
    assert "zz-frontier-runs" in asked.edits[0]
    assert any("Answered in the project you picked." in a for a in _answers(slack))
    async with AsyncSession(env.engine) as db:
        assert (await db.exec(select(SlackProjectAskTable))).all() == []


async def test_a_question_about_the_project_survives_a_restart(
    env, on, slack, session, credential
):
    await link(session, "U1")
    await project(session, "zz-frontier-runs")
    await _for_new_chats(env, [say("Still answered.")])
    await service.start(host="lab-mac")
    await slack.deliver(_dm("how is the Frontier queue?"))
    asked, buttons = _picker(slack)

    await service.stop()
    await service.start(host="lab-mac")
    await slack.deliver(_tap(asked, buttons[env.project.name]))

    (chat,) = await _slack_chats(env)
    await _settled(env, chat.id)
    assert any("Still answered." in a for a in _answers(slack))


async def test_with_one_project_vista_does_not_ask(env, on, slack, session, credential):
    await link(session, "U1")
    await _for_new_chats(env, [say("Straight in.")])
    await service.start(host="lab-mac")

    await slack.deliver(_dm("how is the Frontier queue?"))
    (chat,) = await _slack_chats(env)
    await _settled(env, chat.id)

    assert not any(m.text == service.WHICH_PROJECT for m in slack.messages)
    assert any("Straight in." in a for a in _answers(slack))


async def test_lock_from_slack_and_unlock_only_from_desktop(on, slack, session):
    await link(session, "U1")
    svc = _service()
    await svc.start()

    await slack.deliver(_dm("lock"))
    await slack.deliver(_dm("unlock", ts="11.0"))

    async with AsyncSession(session.bind) as s:
        config = await s.get(SlackConfigTable, 1)
        assert config is not None and config.locked
        assert config.locked_by == "slack:U1"
    texts = [m.text for m in slack.in_channel("DU1")]
    assert limits.LOCKED_REPLY in texts and limits.UNLOCK_REPLY in texts
    assert "Locked" in json.dumps(slack.homes["U1"])
    await svc.stop()


async def test_an_unlinked_user_is_refused_and_nothing_runs(
    on, slack, session, monkeypatch
):
    await link(session, "U1")

    async def never(*a, **kw):
        raise AssertionError("no turn for an unlinked user")

    monkeypatch.setattr(inbox, "put", never)
    svc = _service()
    await svc.start()

    await slack.deliver(_dm("submit a job", user="U2"))

    assert any("isn't linked" in m.text for m in slack.in_channel("DU2"))
    await svc.stop()


async def test_link_issues_a_code_in_the_dm(on, slack, session):
    await link(session, "U1")
    svc = _service()
    await svc.start()

    await slack.deliver(_dm("link", user="U9"))

    (msg,) = slack.in_channel("DU9")
    assert "link code is" in msg.text
    await svc.stop()


async def test_home_opened_publishes(on, slack, session):
    await link(session, "U1")
    svc = _service()
    await svc.start()
    slack.homes.clear()

    await slack.deliver(HomeOpened(team_id=TEAM, user_id="U1"))

    assert "U1" in slack.homes
    await svc.stop()


async def test_preview_reads_the_mcp_preview(db, session, monkeypatch):
    p = await project(session)
    project_id = p.id
    from vista_backend.db.schemas import ChatSessionTable

    chat = ChatSessionTable(project_id=project_id, message_history=[], messages=[])
    chat_id = chat.id
    session.add(chat)
    await session.commit()
    asked = []

    def builder(local, paths):
        async def invoke(name, args):
            asked.append(name)
            return (
                "cluster: frontier\naccount: MAT123\nnodes: 2\n"
                "duration: 1:00:00 (3600s)\nscript: flibe-md/run.sh --steps 1"
            )

        return invoke

    monkeypatch.setattr(service, "build_mcp_invoke", builder)

    fields = await service.preview_job(
        chat_id, "vista__submit_hpc_job", {"job": "flibe-md"}
    )

    assert asked == ["preview_hpc_job"]
    assert fields["Account"] == "MAT123" and fields["System"] == "frontier"
    assert await service.preview_job(chat_id, "run_bash", {}) == {}
