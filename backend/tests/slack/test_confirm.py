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


async def _chat(session) -> uuid.UUID:
    p = await project(session)
    row = ChatSessionTable(project_id=p.id, message_history=[], messages=[])
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


async def _offer(slack, session, **kw) -> Confirmations:
    confirmations = Confirmations(slack)
    await confirmations.offer(
        _approval(**kw),
        place=PLACE,
        team_id=TEAM,
        chat_session_id=await _chat(session),
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


async def test_the_card_shows_what_would_be_submitted(answers, session):
    slack = FakeTransport()

    confirmations = await _offer(slack, session)

    (card,) = slack.messages
    assert card.thread_ts == "10.0"
    text = _card_text(card)
    for value in ["frontier", "2", "1:00:00", "flibe-md --steps 1000"]:
        assert value in text
    assert CONFIRM in text and CANCEL in text
    assert "Only <@U1> can confirm" in text
    confirmations.stop()


async def test_the_preview_adds_the_account(answers, session, monkeypatch):
    async def preview(chat_session_id, tool, args):
        assert isinstance(chat_session_id, uuid.UUID)
        assert tool == "vista__submit_hpc_job" and args == SUBMIT
        return {"Account": "MAT123"}

    monkeypatch.setattr(confirm, "_preview", preview)
    slack = FakeTransport()

    confirmations = await _offer(slack, session)

    assert "MAT123" in _card_text(slack.messages[0])
    confirmations.stop()


async def test_confirm_by_the_requester_goes_ahead(answers, engine, session):
    slack = FakeTransport()
    confirmations = await _offer(slack, session)

    await confirmations.on_action(_tap())

    assert answers == [("e1", "accept")]
    assert "Confirmed by <@U1>" in _card_text(slack.messages[0])
    assert CONFIRM not in _card_text(slack.messages[0]), "the buttons are gone"
    async with AsyncSession(engine) as s:
        row = await s.get(SlackPendingTable, "e1")
        assert row is not None and row.outcome == "confirmed"


async def test_someone_else_cannot_confirm(answers, session):
    slack = FakeTransport()
    confirmations = await _offer(slack, session)

    await confirmations.on_action(_tap(user="U2"))

    assert answers == []
    assert slack.ephemerals == [("DU1", "U2", "Only <@U1> can answer this request.")]
    assert CONFIRM in _card_text(slack.messages[0]), "still open for the requester"
    confirmations.stop()


async def test_cancel_declines(answers, session):
    slack = FakeTransport()
    confirmations = await _offer(slack, session)

    await confirmations.on_action(_tap(CANCEL))

    assert answers == [("e1", "decline")]
    assert "Cancelled by <@U1>" in _card_text(slack.messages[0])


async def test_a_second_tap_does_nothing(answers, session):
    slack = FakeTransport()
    confirmations = await _offer(slack, session)

    await confirmations.on_action(_tap())
    await confirmations.on_action(_tap(CANCEL))

    assert answers == [("e1", "accept")]
    assert slack.ephemerals[-1][2] == "That request is already closed."


async def test_an_unanswered_card_expires(answers, session, monkeypatch):
    monkeypatch.setattr(settings.slack, "confirmation_ttl", 0.01)
    slack = FakeTransport()
    confirmations = await _offer(slack, session)

    # The decline is recorded before the card is edited: wait for both.
    for _ in range(500):
        if answers and "Expired" in _card_text(slack.messages[0]):
            break
        await asyncio.sleep(0.01)

    assert answers == [("e1", "decline")]
    assert "Expired" in _card_text(slack.messages[0])
    await confirmations.on_action(_tap())
    assert answers == [("e1", "decline")], "a tap after expiry does nothing"


async def test_a_tap_after_the_deadline_is_refused(answers, session, monkeypatch):
    slack = FakeTransport()
    confirmations = await _offer(slack, session)
    later = now() + timedelta(seconds=settings.slack.confirmation_ttl + 1)
    monkeypatch.setattr(confirm, "now", lambda: later)

    await confirmations.on_action(_tap())

    assert answers == [("e1", "decline")]
    assert slack.ephemerals[-1][2] == "That request expired. Nothing was done."


async def test_lock_and_limits_are_checked_again_on_confirm(
    answers, session, monkeypatch
):
    slack = FakeTransport()
    confirmations = await _offer(slack, session)

    async def locked(tool, args, **_):
        assert args == SUBMIT
        return "Slack is locked. Unlock it from the VISTA desktop app."

    monkeypatch.setattr(gate, "_refusal", locked)

    await confirmations.on_action(_tap())

    assert answers == [("e1", "decline")]
    assert "Slack is locked" in _card_text(slack.messages[0])


async def test_a_confirm_for_a_run_that_is_gone_says_nothing_ran(
    answers, engine, session, monkeypatch
):
    async def gone(elicitation_id, action, content=None):
        return False

    monkeypatch.setattr(chat_run, "resolve_prompt", gone)
    slack = FakeTransport()
    confirmations = await _offer(slack, session)

    await confirmations.on_action(_tap())

    assert "Expired" in _card_text(slack.messages[0])


async def test_cards_left_open_at_a_restart_are_expired(answers, session):
    slack = FakeTransport()
    first = await _offer(slack, session)
    first.stop()  # VISTA quit.

    assert await Confirmations(slack).close_stale() == 1
    assert "Expired" in _card_text(slack.messages[0])


async def test_a_tap_from_another_workspace_is_ignored(answers, session):
    slack = FakeTransport()
    confirmations = await _offer(slack, session)

    tap = _tap()
    await confirmations.on_action(Action(**{**tap.__dict__, "team_id": "T9999"}))

    assert answers == []
    confirmations.stop()


async def test_url_prompts_are_declined_not_shown(answers, session):
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
        chat_session_id=await _chat(session),
    )

    assert slack.messages == [] and answers == [("u1", "decline")]


def test_fields_for_other_tools():
    assert fields_for("vista__cancel_hpc_job", {"job_id": "42", "cluster": "lux"}) == {
        "System": "lux",
        "Job": "42",
    }
    long = fields_for("run_bash", {"command": "x" * 1000})
    assert len(long["command"]) <= 300


# ---------------------------------------------------------------------------
# Group 5
# ---------------------------------------------------------------------------


async def test_only_the_confirm_button_confirms(answers, session):
    slack = FakeTransport()
    confirmations = await _offer(slack, session)

    await confirmations.on_action(_tap(action_id="vista_choose_0"))
    await confirmations.on_action(_tap(action_id="vista_show_more"))

    assert answers == [], "a tap on anything but Confirm or Cancel answers nothing"
    await confirmations.on_action(_tap())
    assert answers == [("e1", "accept")]


async def test_a_card_in_a_turn_vista_started_mentions_its_user(answers, session):
    slack = FakeTransport()
    confirmations = Confirmations(slack)
    await confirmations.offer(
        _approval(),
        place=PLACE,
        team_id=TEAM,
        chat_session_id=await _chat(session),
        mention=True,
    )
    (posted,) = slack.messages
    assert posted.blocks[0]["text"]["text"].startswith("<@U1> *Confirm")

    await confirmations.on_action(_tap(action_id=CANCEL))
    assert "<@U1> *Confirm" in _card_text(slack.find(posted.ts)), "kept on redraw"


async def test_a_card_in_a_requested_turn_does_not_mention(answers, session):
    slack = FakeTransport()
    await _offer(slack, session)
    assert slack.messages[0].blocks[0]["text"]["text"].startswith("*Confirm")


def test_cancellation_and_agenthpc_cards_show_what_they_act_on():
    assert fields_for(
        "vista__cancel_hpc_job", {"cluster": "odo", "job_id": "4471"}
    ) == {
        "System": "odo",
        "Job": "4471",
    }
    submit = fields_for(
        "vista__agenthpc_submit_parameter_set",
        {
            "app_type": "monbtaw",
            "parameters": [0.25, 0.25, 0.25, 0.25],
            "trial_number": 3,
        },
    )
    assert submit == {
        "Application": "monbtaw",
        "Parameters": "0.25, 0.25, 0.25, 0.25",
        "Trial": "3",
    }
    assert (
        fields_for("vista__agenthpc_cancel_all_pending", {"app_type": "monbtaw"})[
            "Jobs"
        ]
        == "every job still in flight"
    )


async def test_the_confirm_recheck_sees_the_conversations_preview(
    answers, session, monkeypatch
):
    seen = []

    async def refusal(tool, args, *, chat_session_id=None):
        seen.append(chat_session_id)
        return None

    monkeypatch.setattr(gate, "_refusal", refusal)
    slack = FakeTransport()
    confirmations = await _offer(slack, session)
    await confirmations.on_action(_tap())
    assert seen and seen[0] is not None
