"""The Slack service: nothing starts while Slack is off; on, events reach the agent."""

import asyncio
import json

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.db.schemas import SlackAuditTable, SlackConfigTable
from vista_backend.services import chat_run, conversation
from vista_backend.services.chat_run import ChatRun
from vista_backend.slack import confirm, gate, limits, service, store
from vista_backend.slack.events import HomeOpened, Message
from vista_backend.slack.lease import EVENT_TYPE
from vista_backend.slack.notices import offline_text
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
    return SlackService(host="lab-mac", watch_power=False)


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


async def test_not_this_machine_or_no_tokens(db, slack, session, slack_config):
    row = await store.get_config(session)
    row.handle_here = False
    session.add(row)
    await session.commit()
    svc = _service()
    assert await svc.start() is False
    assert svc.detail == "This machine is not set to handle Slack."

    row = await store.get_config(session)
    row.handle_here = True
    session.add(row)
    await session.commit()
    assert await svc.start() is False
    assert "token" in (svc.detail or "")
    assert slack.made == []  # type: ignore[attr-defined]


async def test_lifespan_module_start_returns_none_when_off(db, slack):
    assert await service.start(watch_power=False) is None
    assert service.status() == ("off", "Slack is off.")
    await service.stop()


async def test_on_it_connects_and_installs_the_checks(on, slack, session, alice):
    await link(session, alice, "U1")
    svc = _service()

    assert await svc.start() is True

    assert slack.made == [(BOT, APP)]  # type: ignore[attr-defined]
    assert slack.connected and svc.state == "connected"
    assert gate._refusal is limits.refusal
    assert confirm._preview is service.preview_job
    assert "U1" in slack.homes, "the Home tab is published on connect"
    assert slack.pins.get("DU1"), "the lease is pinned in the first DM"

    await svc.stop()
    assert not slack.connected and svc.state == "off"
    assert gate._refusal is gate.no_refusal
    assert svc._tasks == []


async def test_stop_says_offline_and_releases_the_lease(on, slack, session, alice):
    await link(session, alice, "U1")
    svc = _service()
    await svc.start()

    await svc.stop()

    texts = [m.text for m in slack.in_channel("DU1")]
    assert offline_text("lab-mac") in texts
    assert "Offline" in json.dumps(slack.homes["U1"])
    status = next(m for m in slack.messages if m.metadata)
    assert status.metadata["event_payload"]["released"] is True


async def test_another_machine_has_slack_so_stand_by(on, slack, session, alice):
    await link(session, alice, "U1")
    other = {
        "event_type": EVENT_TYPE,
        "event_payload": {
            "instance_id": "other",
            "host": "office-linux",
            "heartbeat_at": store.now_iso(),
            "released": False,
        },
    }
    await slack.pin("DU1", await slack.post("DU1", "VISTA status", metadata=other))
    svc = _service()

    assert await svc.start() is True

    assert not slack.connected
    assert svc.state == "standby" and "office-linux" in (svc.detail or "")

    await svc.take_over()
    assert slack.connected and svc.state == "connected"
    await svc.stop()


async def test_a_dm_runs_a_slack_turn(on, slack, session, alice, monkeypatch):
    await link(session, alice, "U1")
    await project(session, alice)
    started: list[dict] = []

    async def fake_start(db, **kw):
        started.append(kw)
        run = ChatRun(
            chat_session_id=kw["chat_session_id"],
            project_id=kw["project"].id,
            user_id=kw["user"].id,
            agent_key=("k",),  # type: ignore[arg-type]
            user_prompt=kw["user_prompt"],
            prior_history=[],
            channel=kw["channel"],
        )

        async def finish():
            await run.append(
                "agent_run_result",
                json.dumps(
                    {
                        "event_kind": "agent_run_result",
                        "result": {
                            "new_messages": [
                                {
                                    "kind": "response",
                                    "parts": [
                                        {
                                            "part_kind": "text",
                                            "content": "Queue is short.",
                                        }
                                    ],
                                }
                            ]
                        },
                    }
                ),
            )
            await run.append(
                "run_finished",
                json.dumps({"event_kind": "run_finished", "state": "done"}),
            )
            await run._finish()

        asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(finish()))
        return run

    monkeypatch.setattr(service, "start_turn", fake_start)
    svc = _service()
    await svc.start()

    await slack.deliver(_dm("<@UBOT> how is the Frontier queue?"))

    (kw,) = started
    assert kw["channel"] == "slack" and kw["user"].id == alice.id
    assert kw["user_prompt"] == "how is the Frontier queue?"
    answer = [m for m in slack.in_channel("DU1") if m.thread_ts == "10.0"]
    assert answer and "Queue is short." in (answer[-1].edits or [answer[-1].text])[-1]
    async with AsyncSession(session.bind) as s:
        events = [r.event for r in (await s.exec(select(SlackAuditTable))).all()]
    assert "request" in events
    await svc.stop()


async def test_lock_from_slack_and_unlock_only_from_desktop(on, slack, session, alice):
    await link(session, alice, "U1")
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
    on, slack, session, alice, monkeypatch
):
    await link(session, alice, "U1")

    async def never(*a, **kw):
        raise AssertionError("no turn for an unlinked user")

    monkeypatch.setattr(service, "start_turn", never)
    svc = _service()
    await svc.start()

    await slack.deliver(_dm("submit a job", user="U2"))

    assert any("isn't linked" in m.text for m in slack.in_channel("DU2"))
    await svc.stop()


async def test_link_issues_a_code_in_the_dm(on, slack, session, alice):
    await link(session, alice, "U1")
    svc = _service()
    await svc.start()

    await slack.deliver(_dm("link", user="U9"))

    (msg,) = slack.in_channel("DU9")
    assert "link code is" in msg.text
    await svc.stop()


async def test_home_opened_publishes(on, slack, session, alice):
    await link(session, alice, "U1")
    svc = _service()
    await svc.start()
    slack.homes.clear()

    await slack.deliver(HomeOpened(team_id=TEAM, user_id="U1"))

    assert "U1" in slack.homes
    await svc.stop()


async def test_preview_reads_the_mcp_preview(db, session, alice, monkeypatch):
    p = await project(session, alice)
    project_id = p.id
    from vista_backend.db.schemas import ChatSessionTable

    chat = ChatSessionTable(
        project_id=project_id, user_id=alice.id, message_history=[], messages=[]
    )
    chat_id = chat.id
    session.add(chat)
    await session.commit()
    asked = []

    def builder(user, paths):
        async def invoke(name, args):
            asked.append(name)
            return (
                "cluster: frontier\naccount: MAT123\nnodes: 2\n"
                "duration: 1:00:00 (3600s)\nscript: flibe-md/run.sh --steps 1"
            )

        return invoke

    monkeypatch.setattr(service, "build_mcp_invoke", builder)

    fields = await service.preview_job(
        alice.id, chat_id, "vista__submit_hpc_job", {"job": "flibe-md"}
    )

    assert asked == ["preview_hpc_job"]
    assert fields["Account"] == "MAT123" and fields["System"] == "frontier"
    assert await service.preview_job(alice.id, chat_id, "run_bash", {}) == {}


def test_the_conversation_module_is_what_is_patched():
    """`service.start_turn` is the shared entry point, not a Slack copy."""
    assert service.start_turn is conversation.start_turn
