"""`offer_choices`: buttons for an ambiguous request, in Slack turns only."""

import asyncio
import json
import uuid

import pytest
from pydantic_ai.messages import FunctionToolResultEvent, ModelResponse, TextPart
from sqlmodel.ext.asyncio.session import AsyncSession

from harness import call, make_project, make_user, say, scripted_model, step_model
from vista_backend.agents.agents import McpFormElicitationEvent, ProjectAgent
from vista_backend.config import settings
from vista_backend.db.schemas import ChatSessionTable
from vista_backend.services import chat_run
from vista_backend.services.chat_run import RunEvent
from vista_backend.slack.choices import choice_schema, options_of
from vista_backend.slack.confirm import CHOOSE, Confirmations
from vista_backend.slack.events import Action
from vista_backend.slack.relay import Place

from .conftest import TEAM, project
from .fakes import FakeTransport

pytestmark = pytest.mark.anyio

PLACE = Place(channel="DU1", thread_ts="10.0", slack_user_id="U1")
JOBS = ["flibe-md 4471", "flibe-md 4470", "nacl-dft 4466"]


def test_a_choice_schema_is_recognised_and_a_form_is_not():
    assert options_of(choice_schema(JOBS)) == JOBS
    assert options_of({"properties": {"password": {"type": "string"}}}) is None
    assert options_of(None) is None


async def _turn(channel, args, answer=None):
    agent = ProjectAgent(make_project(tools=["*"]), make_user(), uuid.uuid4())
    events = []
    model = step_model([call("offer_choices", **args), say("done")])
    with agent.agent.override(model=model, toolsets=[]):
        async for event in agent.run_stream(
            user_prompt="status of my flibe job?",
            enable_elicitation=True,
            channel=channel,
        ):
            events.append(event)
            if isinstance(event, McpFormElicitationEvent) and answer is not None:
                agent.resolve_elicitation(event.elicitation_id, *answer)
    return events


def _results(events):
    return [
        str(getattr(e.part, "content", ""))
        for e in events
        if isinstance(e, FunctionToolResultEvent)
    ]


async def test_the_tap_is_returned_to_the_model():
    events = await _turn(
        "slack",
        {"question": "Which job?", "options": JOBS},
        answer=("accept", {"choice": JOBS[1]}),
    )

    (form,) = [e for e in events if isinstance(e, McpFormElicitationEvent)]
    assert form.message == "Which job?"
    assert options_of(dict(form.requested_schema)) == JOBS
    assert any(f"picked: {JOBS[1]}" in r for r in _results(events))


async def test_an_option_not_offered_counts_as_none():
    events = await _turn(
        "slack",
        {"question": "Which job?", "options": JOBS},
        answer=("accept", {"choice": "rm -rf /"}),
    )

    assert any("didn't pick" in r for r in _results(events))


async def test_more_than_five_options_are_refused_without_asking():
    events = await _turn(
        "slack", {"question": "Which?", "options": [f"job {i}" for i in range(6)]}
    )

    assert not [e for e in events if isinstance(e, McpFormElicitationEvent)]
    assert any("At most 5" in r for r in _results(events))


async def test_the_tool_is_hidden_outside_slack():
    agent = ProjectAgent(make_project(tools=["*"]), make_user(), uuid.uuid4())
    seen: dict[str, list[str]] = {}

    def model(messages, info):
        seen[agent.channel] = [t.name for t in info.function_tools]
        return ModelResponse(parts=[TextPart("ok")])

    for channel in ["slack", "app"]:
        with agent.agent.override(model=scripted_model(model), toolsets=[]):
            async for _ in agent.run_stream(user_prompt="hi", channel=channel):
                pass

    assert "offer_choices" in seen["slack"]
    assert "offer_choices" not in seen["app"]


# Slack side: the buttons, and who may tap them.


@pytest.fixture
def answers(engine, monkeypatch):
    monkeypatch.setattr(chat_run, "session_factory", lambda: AsyncSession(engine))
    told: list[tuple[str, str, dict | None]] = []

    async def resolve(elicitation_id, action, content=None):
        told.append((elicitation_id, action, content))
        return True

    monkeypatch.setattr(chat_run, "resolve_prompt", resolve)
    return told


async def _offer(slack, session, alice) -> Confirmations:
    p = await project(session, alice)
    chat = ChatSessionTable(
        project_id=p.id, user_id=alice.id, message_history=[], messages=[]
    )
    session.add(chat)
    await session.commit()
    await session.refresh(chat)
    data = {
        "event_kind": "mcp_form_elicitation",
        "elicitation_id": "c1",
        "message": "Which job?",
        "requested_schema": choice_schema(JOBS),
    }
    confirmations = Confirmations(slack)
    await confirmations.offer(
        RunEvent(
            seq=1, kind="mcp_form_elicitation", data=json.dumps(data), prompt_id="c1"
        ),
        place=PLACE,
        team_id=TEAM,
        chat_session_id=chat.id,
        user_id=alice.id,
    )
    return confirmations


def _tap(index, user="U1") -> Action:
    return Action(
        team_id=TEAM,
        channel_id="DU1",
        user_id=user,
        action_id=f"{CHOOSE}{index}",
        value="c1",
        message_ts="1.000",
        thread_ts="10.0",
    )


async def test_one_button_per_option(answers, session, alice):
    slack = FakeTransport()
    confirmations = await _offer(slack, session, alice)

    (posted,) = slack.messages
    (actions,) = [b for b in posted.blocks if b["type"] == "actions"]
    assert [e["text"]["text"] for e in actions["elements"]] == JOBS
    assert posted.thread_ts == "10.0"
    confirmations.stop()


async def test_the_requesters_tap_answers_with_the_option(answers, session, alice):
    slack = FakeTransport()
    confirmations = await _offer(slack, session, alice)

    await confirmations.on_action(_tap(2))

    assert answers == [("c1", "accept", {"choice": JOBS[2]})]
    text = json.dumps(slack.messages[0].blocks)
    assert "chose *nacl-dft 4466*" in text and CHOOSE not in text


async def test_someone_elses_tap_does_not_count(answers, session, alice):
    slack = FakeTransport()
    confirmations = await _offer(slack, session, alice)

    await confirmations.on_action(_tap(0, user="U2"))

    assert answers == []
    assert slack.ephemerals == [("DU1", "U2", "Only <@U1> can answer this request.")]
    confirmations.stop()


async def test_an_index_out_of_range_is_ignored(answers, session, alice):
    slack = FakeTransport()
    confirmations = await _offer(slack, session, alice)

    await confirmations.on_action(_tap(7))

    assert answers == []
    confirmations.stop()


async def test_an_unanswered_choice_expires(answers, session, alice, monkeypatch):
    monkeypatch.setattr(settings.slack, "confirmation_ttl", 0.01)
    slack = FakeTransport()
    await _offer(slack, session, alice)

    for _ in range(100):
        if answers:
            break
        await asyncio.sleep(0.01)

    assert answers == [("c1", "decline", None)]
    assert "Expired." in json.dumps(slack.messages[0].blocks)
