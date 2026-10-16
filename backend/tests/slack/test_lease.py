"""One machine handles Slack: the lease pinned in Slack, and taking over."""

from datetime import timedelta

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.services import chat_run
from vista_backend.slack import lease, store
from vista_backend.slack.lease import Lease

from .conftest import link
from .fakes import FakeTransport

pytestmark = pytest.mark.anyio


@pytest.fixture
async def slack(engine, session, alice, monkeypatch):
    """One Slack workspace both machines see, with a linked researcher."""
    monkeypatch.setattr(chat_run, "session_factory", lambda: AsyncSession(engine))
    await link(session, alice, "U1")
    return FakeTransport()


def _machines(slack):
    return (
        Lease(slack, instance_id="A", host="lab-mac"),
        Lease(slack, instance_id="B", host="home-linux"),
    )


async def _status(engine):
    async with AsyncSession(engine) as s:
        return (await store.get_config(s)).lease_status


async def test_the_first_machine_takes_it_and_pins_the_status(slack):
    a, _ = _machines(slack)

    assert (await a.acquire()).ours

    (msg,) = slack.messages
    assert msg.channel == "DU1" and slack.pins["DU1"] == [msg.ts]
    assert msg.metadata["event_payload"]["instance_id"] == "A"
    assert "lab-mac" in msg.text


async def test_a_second_machine_stays_off_and_names_the_first(engine, slack):
    a, b = _machines(slack)
    await a.acquire()

    decision = await b.acquire()

    assert not decision.ours and decision.holder.host == "lab-mac"
    assert len(slack.messages) == 1, "the second machine wrote nothing"
    status = await _status(engine)
    assert status["ours"] is False and status["host"] == "lab-mac"


async def test_a_heartbeat_edits_the_same_message(slack):
    a, _ = _machines(slack)
    await a.acquire()

    assert await a.heartbeat()

    (msg,) = slack.messages
    assert len(msg.edits) == 1, "an edit, which notifies no one"


async def test_taking_over_makes_the_old_holder_let_go(slack):
    a, b = _machines(slack)
    await a.acquire()

    await b.take_over()

    assert not await a.heartbeat(), "A sees B's id and disconnects"
    assert await b.heartbeat()
    assert len(slack.messages) == 1


async def test_a_silent_holder_goes_stale(slack, monkeypatch):
    a, b = _machines(slack)
    await a.acquire()  # A then crashed: no heartbeat, no release.
    later = store.now() + lease.STALE + timedelta(seconds=1)
    monkeypatch.setattr(lease, "now", lambda: later)

    assert (await b.acquire()).ours
    assert slack.messages[0].metadata["event_payload"]["instance_id"] == "B"


async def test_a_clean_stop_frees_it_at_once(slack):
    a, b = _machines(slack)
    await a.acquire()

    await a.release()

    assert (await b.acquire()).ours


async def test_the_same_machine_after_a_restart_keeps_it(slack):
    a, _ = _machines(slack)
    await a.acquire()

    again = Lease(slack, instance_id="A", host="lab-mac")
    assert (await again.acquire()).ours
    assert len(slack.messages) == 1


async def test_with_no_linked_researcher_there_is_no_lease(engine, monkeypatch):
    monkeypatch.setattr(chat_run, "session_factory", lambda: AsyncSession(engine))
    slack = FakeTransport()

    assert (await Lease(slack, instance_id="A").acquire()).ours
    assert slack.messages == []


async def test_other_pins_are_ignored(slack):
    await slack.pin("DU1", await slack.post("DU1", "a pinned note"))
    a, b = _machines(slack)
    await a.acquire()

    assert not (await b.acquire()).ours
