"""Confirm/Cancel cards: requester only, 10-minute expiry, rechecked on Confirm."""

import asyncio
import json
import uuid
from datetime import timedelta

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.config import settings
from vista_backend.db.schemas import ChatSessionTable, SlackPendingTable
from vista_backend.services import chat_run
from vista_backend.services.chat_run import RunEvent
from vista_backend.slack import confirm, gate
from vista_backend.slack.confirm import CANCEL, CONFIRM, Confirmations, fields_for
from vista_backend.slack.events import Action
from vista_backend.slack.relay import Place
from vista_backend.slack.store import now

from .conftest import TEAM, project
from .fakes import FakeTransport

pytestmark = pytest.mark.anyio

PLACE = Place(channel="DU1", thread_ts="10.0", slack_user_id="U1")
SUBMIT = {
    "job": "flibe-md",
    "cluster": "frontier",
    "node_count": 2,
    "duration": "1:00:00",
    "script_args": "--steps 1000",
}


@pytest.fixture
def answers(engine, monkeypatch):
    """Point the run's DB at the test engine, and record what each prompt is told."""
    monkeypatch.setattr(chat_run, "session_factory", lambda: AsyncSession(engine))
    told: list[tuple[str, str]] = []

    async def resolve(elicitation_id, action, content=None):
        told.append((elicitation_id, action))
        return True

    monkeypatch.setattr(chat_run, "resolve_prompt", resolve)
    return told


async def _chat(session, alice) -> uuid.UUID:
    p = await project(session, alice)
    row = ChatSessionTable(
        project_id=p.id, user_id=alice.id, message_history=[], messages=[]
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row.id


def _approval(tool="vista__submit_hpc_job", args=SUBMIT, eid="e1") -> RunEvent:
    data = {
        "event_kind": "mcp_tool_approval",
        "elicitation_id": eid,
        "tool_name": tool,
        "args": args,
    }
    return RunEvent(
        seq=1, kind="mcp_tool_approval", data=json.dumps(data), prompt_id=eid
    )


async def _offer(slack, session, alice, **kw) -> Confirmations:
    confirmations = Confirmations(slack)
    await confirmations.offer(
        _approval(**kw),
        place=PLACE,
        team_id=TEAM,
        chat_session_id=await _chat(session, alice),
        user_id=alice.id,
    )
    return confirmations


def _tap(action_id=CONFIRM, user="U1", value="e1") -> Action:
    return Action(
        team_id=TEAM,
        channel_id="DU1",
        user_id=user,
        action_id=action_id,
        value=value,
        message_ts="1.000",
        thread_ts="10.0",
    )


def _card_text(posted) -> str:
    return json.dumps(posted.blocks)


async def test_the_card_shows_what_would_be_submitted(answers, session, alice):
    slack = FakeTransport()

    confirmations = await _offer(slack, session, alice)

    (card,) = slack.messages
    assert card.thread_ts == "10.0"
    text = _card_text(card)
    for value in ["frontier", "2", "1:00:00", "flibe-md --steps 1000"]:
        assert value in text
    assert CONFIRM in text and CANCEL in text
    assert "Only <@U1> can confirm" in text
    confirmations.stop()


async def test_the_preview_adds_the_account(answers, session, alice, monkeypatch):
    async def preview(user_id, tool, args):
        assert user_id == alice.id
        return {"Account": "MAT123"}

    monkeypatch.setattr(confirm, "_preview", preview)
    slack = FakeTransport()

    confirmations = await _offer(slack, session, alice)

    assert "MAT123" in _card_text(slack.messages[0])
    confirmations.stop()


async def test_confirm_by_the_requester_goes_ahead(answers, engine, session, alice):
    slack = FakeTransport()
    confirmations = await _offer(slack, session, alice)

    await confirmations.on_action(_tap())

    assert answers == [("e1", "accept")]
    assert "Confirmed by <@U1>" in _card_text(slack.messages[0])
    assert CONFIRM not in _card_text(slack.messages[0]), "the buttons are gone"
    async with AsyncSession(engine) as s:
        row = await s.get(SlackPendingTable, "e1")
        assert row is not None and row.outcome == "confirmed"


async def test_someone_else_cannot_confirm(answers, session, alice):
    slack = FakeTransport()
    confirmations = await _offer(slack, session, alice)

    await confirmations.on_action(_tap(user="U2"))

    assert answers == []
    assert slack.ephemerals == [("DU1", "U2", "Only <@U1> can answer this request.")]
    assert CONFIRM in _card_text(slack.messages[0]), "still open for the requester"
    confirmations.stop()


async def test_cancel_declines(answers, session, alice):
    slack = FakeTransport()
    confirmations = await _offer(slack, session, alice)

    await confirmations.on_action(_tap(CANCEL))

    assert answers == [("e1", "decline")]
    assert "Cancelled by <@U1>" in _card_text(slack.messages[0])


async def test_a_second_tap_does_nothing(answers, session, alice):
    slack = FakeTransport()
    confirmations = await _offer(slack, session, alice)

    await confirmations.on_action(_tap())
    await confirmations.on_action(_tap(CANCEL))

    assert answers == [("e1", "accept")]
    assert slack.ephemerals[-1][2] == "That request is already closed."


async def test_an_unanswered_card_expires(answers, session, alice, monkeypatch):
    monkeypatch.setattr(settings.slack, "confirmation_ttl", 0.01)
    slack = FakeTransport()
    confirmations = await _offer(slack, session, alice)

    for _ in range(100):
        if answers:
            break
        await asyncio.sleep(0.01)

    assert answers == [("e1", "decline")]
    assert "Expired" in _card_text(slack.messages[0])
    await confirmations.on_action(_tap())
    assert answers == [("e1", "decline")], "a tap after expiry does nothing"


async def test_a_tap_after_the_deadline_is_refused(
    answers, session, alice, monkeypatch
):
    slack = FakeTransport()
    confirmations = await _offer(slack, session, alice)
    later = now() + timedelta(seconds=settings.slack.confirmation_ttl + 1)
    monkeypatch.setattr(confirm, "now", lambda: later)

    await confirmations.on_action(_tap())

    assert answers == [("e1", "decline")]
    assert slack.ephemerals[-1][2] == "That request expired. Nothing was done."


async def test_lock_and_limits_are_checked_again_on_confirm(
    answers, session, alice, monkeypatch
):
    slack = FakeTransport()
    confirmations = await _offer(slack, session, alice)

    async def locked(user_id, tool, args):
        assert args == SUBMIT
        return "Slack is locked. Unlock it from the VISTA desktop app."

    monkeypatch.setattr(gate, "_refusal", locked)

    await confirmations.on_action(_tap())

    assert answers == [("e1", "decline")]
    assert "Slack is locked" in _card_text(slack.messages[0])


async def test_a_confirm_for_a_run_that_is_gone_says_nothing_ran(
    answers, engine, session, alice, monkeypatch
):
    async def gone(elicitation_id, action, content=None):
        return False

    monkeypatch.setattr(chat_run, "resolve_prompt", gone)
    slack = FakeTransport()
    confirmations = await _offer(slack, session, alice)

    await confirmations.on_action(_tap())

    assert "Expired" in _card_text(slack.messages[0])


async def test_cards_left_open_at_a_restart_are_expired(answers, session, alice):
    slack = FakeTransport()
    first = await _offer(slack, session, alice)
    first.stop()  # VISTA quit.

    assert await Confirmations(slack).close_stale() == 1
    assert "Expired" in _card_text(slack.messages[0])


async def test_a_tap_from_another_workspace_is_ignored(answers, session, alice):
    slack = FakeTransport()
    confirmations = await _offer(slack, session, alice)

    tap = _tap()
    await confirmations.on_action(Action(**{**tap.__dict__, "team_id": "T9999"}))

    assert answers == []
    confirmations.stop()


async def test_url_prompts_are_declined_not_shown(answers, session, alice):
    slack = FakeTransport()
    confirmations = Confirmations(slack)
    event = RunEvent(
        seq=1,
        kind="mcp_url_elicitation",
        data=json.dumps({"elicitation_id": "u1", "url": "https://auth.globus.org"}),
        prompt_id="u1",
    )

    await confirmations.offer(
        event,
        place=PLACE,
        team_id=TEAM,
        chat_session_id=await _chat(session, alice),
        user_id=alice.id,
    )

    assert slack.messages == [] and answers == [("u1", "decline")]


def test_fields_for_other_tools():
    assert fields_for("vista__cancel_hpc_job", {"job_id": "42", "cluster": "lux"}) == {
        "System": "lux",
        "Job": "42",
    }
    long = fields_for("run_bash", {"command": "x" * 1000})
    assert len(long["command"]) <= 300
