"""Offline and back-online notices, with the two-minute debounce."""

import asyncio
from datetime import timedelta

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.services import chat_run
from vista_backend.slack import notices, store
from vista_backend.slack.notices import ONLINE_TEXT, Notices

from .conftest import link
from .fakes import FakeTransport

pytestmark = pytest.mark.anyio


@pytest.fixture
async def slack(engine, session, alice, bob, monkeypatch):
    monkeypatch.setattr(chat_run, "session_factory", lambda: AsyncSession(engine))
    await link(session, alice, "U1")
    await link(session, bob, "U2")
    return FakeTransport()


def _texts(slack):
    return [(m.channel, m.text) for m in slack.messages]


async def test_quitting_tells_every_linked_researcher(slack):
    await Notices(slack, host="lab-mac").going_offline()

    expected = (
        ":zzz: Your VISTA agent is going offline (host: lab-mac). "
        "Messages sent while it's offline won't be received."
    )
    assert _texts(slack) == [("DU1", expected), ("DU2", expected)]


async def test_back_online_after_a_notice_once_two_minutes_have_passed(
    slack, monkeypatch
):
    first = Notices(slack, host="lab-mac")
    await first.going_offline()
    later = store.now() + notices.DEBOUNCE + timedelta(seconds=1)
    monkeypatch.setattr(notices, "now", lambda: later)

    second = Notices(slack, host="lab-mac")  # VISTA started again.
    await second.connected()
    await second.wait()

    assert [t for _, t in _texts(slack)[2:]] == [ONLINE_TEXT, ONLINE_TEXT]
    await second.connected()
    await second.wait()
    assert len(slack.messages) == 4, "said once, not on every reconnect"


async def test_a_quick_restart_waits_out_the_two_minutes(slack, monkeypatch):
    monkeypatch.setattr(notices, "DEBOUNCE", timedelta(seconds=0.2))
    await Notices(slack).going_offline()

    again = Notices(slack)
    await again.connected()
    await asyncio.sleep(0.05)
    assert len(slack.messages) == 2, "nothing yet"

    await again.wait()
    assert [t for _, t in _texts(slack)[2:]] == [ONLINE_TEXT, ONLINE_TEXT]


async def test_going_away_again_inside_the_two_minutes_says_nothing(slack, monkeypatch):
    monkeypatch.setattr(notices, "DEBOUNCE", timedelta(seconds=0.2))
    await Notices(slack).going_offline()
    again = Notices(slack)
    await again.connected()

    again.cancel()  # Dropped before the two minutes were up.
    await asyncio.sleep(0.3)

    assert len(slack.messages) == 2


async def test_a_dropped_connection_with_no_notice_says_nothing(engine, slack):
    n = Notices(slack)
    await n.connected()
    await n.wait()

    assert slack.messages == []
    async with AsyncSession(engine) as s:
        assert (await store.get_config(s)).online_since is not None


async def test_a_slow_slack_cannot_hold_up_a_stop(slack, monkeypatch):
    monkeypatch.setattr(notices, "SHUTDOWN_BUDGET", 0.05)

    async def slow(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(slack, "post", slow)

    await asyncio.wait_for(Notices(slack).going_offline(), timeout=1)


async def test_one_failed_dm_does_not_stop_the_others(slack):
    slack.fail_next["post"] = "channel_not_found"

    await Notices(slack, host="h").going_offline()

    assert [c for c, _ in _texts(slack)] == ["DU2"]
