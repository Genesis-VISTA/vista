"""
The Socket Mode transport, without a socket: a Socket Mode request is run
through the real Bolt app, which must ack it and pass VISTA a normalized event.
"""

import asyncio

import pytest
from slack_bolt.adapter.socket_mode.async_internals import run_async_bolt_app
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse

from vista_backend.slack.events import Action, HomeOpened, Message
from vista_backend.slack.socket_mode import SocketModeTransport


@pytest.fixture
def transport(monkeypatch):
    async def auth_test(self, **_):
        return AsyncSlackResponse(
            client=self,
            http_verb="POST",
            api_url="https://slack.com/api/auth.test",
            req_args={},
            data={"ok": True, "team_id": "T0001", "user_id": "UBOT", "bot_id": "B1"},
            headers={},
            status_code=200,
        )

    # Bolt makes a client per request, so the class is patched, not ours.
    monkeypatch.setattr(AsyncWebClient, "auth_test", auth_test)
    return SocketModeTransport(bot_token="xoxb-test", app_token="xapp-test")


async def _run(transport, payload, kind="events_api"):
    seen = []

    async def handler(event):
        seen.append(event)

    transport._handler = handler
    req = SocketModeRequest(type=kind, envelope_id="e1", payload=payload)
    response = await run_async_bolt_app(transport.app, req)
    await asyncio.sleep(0)
    await asyncio.gather(*transport._tasks)
    return response, seen


def _event(event, event_type=None):
    return {
        "type": "event_callback",
        "team_id": "T0001",
        "api_app_id": "A1",
        "event": event,
        "event_id": "Ev1",
        "event_time": 1,
    }


@pytest.mark.anyio
async def test_a_dm_is_acked_and_dispatched(transport):
    response, seen = await _run(
        transport,
        _event(
            {
                "type": "message",
                "channel_type": "im",
                "channel": "D1",
                "user": "U1",
                "text": "queue on Frontier?",
                "ts": "1.1",
            }
        ),
    )
    assert response.status == 200
    assert seen == [
        Message(
            team_id="T0001",
            channel_id="D1",
            user_id="U1",
            text="queue on Frontier?",
            ts="1.1",
            is_dm=True,
        )
    ]


@pytest.mark.anyio
async def test_a_mention_in_a_thread_keeps_its_thread(transport):
    _, seen = await _run(
        transport,
        _event(
            {
                "type": "app_mention",
                "channel": "C1",
                "user": "U1",
                "text": "<@UBOT> status?",
                "ts": "2.2",
                "thread_ts": "1.0",
            }
        ),
    )
    assert seen[0].thread_ts == "1.0" and seen[0].root_ts == "1.0"
    assert seen[0].prompt == "status?"


@pytest.mark.anyio
async def test_channel_messages_edits_and_bot_posts_are_not_dispatched(transport):
    for event in (
        {
            "type": "message",
            "channel_type": "channel",
            "channel": "C1",
            "user": "U1",
            "text": "hi",
            "ts": "1",
        },
        {
            "type": "message",
            "channel_type": "im",
            "subtype": "message_changed",
            "channel": "D1",
            "ts": "2",
        },
        {
            "type": "message",
            "channel_type": "im",
            "channel": "D1",
            "user": "U2",
            "bot_id": "B2",
            "text": "x",
            "ts": "3",
        },
    ):
        response, seen = await _run(transport, _event(event))
        assert response.status == 200
        assert seen == []


@pytest.mark.anyio
async def test_home_opened(transport):
    _, seen = await _run(
        transport, _event({"type": "app_home_opened", "user": "U1", "tab": "home"})
    )
    assert seen == [HomeOpened(team_id="T0001", user_id="U1")]


@pytest.mark.anyio
async def test_a_button_tap(transport):
    payload = {
        "type": "block_actions",
        "team": {"id": "T0001"},
        "user": {"id": "U1"},
        "api_app_id": "A1",
        "channel": {"id": "C1"},
        "container": {
            "type": "message",
            "message_ts": "5.5",
            "channel_id": "C1",
            "thread_ts": "1.0",
        },
        "message": {"ts": "5.5", "thread_ts": "1.0"},
        "actions": [{"action_id": "vista_confirm", "value": "el-1", "type": "button"}],
        "trigger_id": "t",
    }
    response, seen = await _run(transport, payload, kind="interactive")
    assert response.status == 200
    assert seen == [
        Action(
            team_id="T0001",
            channel_id="C1",
            user_id="U1",
            action_id="vista_confirm",
            value="el-1",
            message_ts="5.5",
            thread_ts="1.0",
        )
    ]


@pytest.mark.anyio
async def test_a_failing_handler_does_not_reach_bolt(transport):
    async def boom(event):
        raise RuntimeError("handler bug")

    transport._handler = boom
    req = SocketModeRequest(
        type="events_api",
        envelope_id="e1",
        payload=_event(
            {
                "type": "app_mention",
                "channel": "C1",
                "user": "U1",
                "text": "hi",
                "ts": "1",
            }
        ),
    )
    response = await run_async_bolt_app(transport.app, req)
    await asyncio.gather(*transport._tasks)
    assert response.status == 200
