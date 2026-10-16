"""Credential reminders at 24 h and 2 h, and refusing after expiry."""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from harness import make_project, say, scripted_model
from vista_backend.agents.agents import ProjectAgent
from vista_backend.services import chat_run
from vista_backend.slack import credentials, limits
from vista_backend.slack.credentials import (
    CredentialReminder,
    Expiry,
    globus_expiries,
    jwt_expiry,
)
from vista_backend.slack.store import now

from .conftest import link
from .fakes import FakeTransport

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def fresh_known(monkeypatch):
    monkeypatch.setattr(credentials, "_known", {})


@pytest.fixture
async def linked(engine, session, alice, monkeypatch):
    monkeypatch.setattr(chat_run, "session_factory", lambda: AsyncSession(engine))
    await link(session, alice, "U1")
    return alice


def _source(*expiries):
    async def expiries_for(user):
        return list(expiries)

    return expiries_for


def _in(hours: float) -> datetime:
    return now() + timedelta(hours=hours)


async def test_a_day_before_one_dm_names_the_system_and_time(linked):
    slack = FakeTransport()
    frontier = Expiry("frontier", "S3M token", _in(23))
    reminder = CredentialReminder(slack, expiries=_source(frontier))

    assert await reminder.run_once() == 1
    assert await reminder.run_once() == 0, "once per window"

    (msg,) = slack.messages
    assert msg.channel == "DU1"
    assert "24 hours" in msg.text and "Frontier S3M token" in msg.text
    assert f"<!date^{int(frontier.expires_at.timestamp())}" in msg.text
    assert "desktop app" in msg.text


async def test_two_hours_before_a_second_reminder(linked, monkeypatch):
    slack = FakeTransport()
    odo = Expiry("odo", "S3M token", _in(23))
    reminder = CredentialReminder(slack, expiries=_source(odo))
    await reminder.run_once()

    later = now() + timedelta(hours=21, minutes=30)
    monkeypatch.setattr(credentials, "now", lambda: later)

    assert await reminder.run_once() == 1
    assert "2 hours" in slack.messages[-1].text
    assert await reminder.run_once() == 0


async def test_a_renewed_credential_gets_its_own_reminders(linked):
    slack = FakeTransport()
    await CredentialReminder(
        slack, expiries=_source(Expiry("frontier", "S3M token", _in(20)))
    ).run_once()

    renewed = Expiry("frontier", "S3M token", _in(22))
    assert await CredentialReminder(slack, expiries=_source(renewed)).run_once() == 1


async def test_far_off_and_already_expired_send_nothing(linked):
    slack = FakeTransport()
    reminder = CredentialReminder(
        slack,
        expiries=_source(
            Expiry("frontier", "S3M token", _in(72)),
            Expiry("odo", "S3M token", _in(-1)),
        ),
    )

    assert await reminder.run_once() == 0
    assert slack.messages == []


async def test_an_estimate_is_labelled(linked):
    slack = FakeTransport()
    await CredentialReminder(
        slack,
        expiries=_source(Expiry("frontier", "Globus session", _in(10), True)),
    ).run_once()

    assert "Frontier Globus session" in slack.messages[0].text
    assert "(estimated)" in slack.messages[0].text


async def test_after_expiry_a_slack_submit_says_what_is_unavailable(
    linked, engine, session
):
    await CredentialReminder(
        FakeTransport(), expiries=_source(Expiry("frontier", "S3M token", _in(-1)))
    ).run_once()

    why = await limits.refusal(
        linked.id, "vista__submit_hpc_job", {"cluster": "frontier", "job": "x"}
    )
    assert why is not None
    assert "Submission, cancellation and outputs on Frontier are unavailable" in why
    assert "desktop app" in why
    assert (
        await limits.refusal(linked.id, "vista__submit_hpc_job", {"cluster": "lux"})
        is None
    ), "other systems still work"
    assert (
        await limits.refusal(
            linked.id, "vista__get_hpc_job_status", {"cluster": "frontier"}
        )
        is None
    ), "read-only status is not refused"


async def test_after_expiry_the_slack_prompt_says_which_systems(linked):
    await CredentialReminder(
        FakeTransport(), expiries=_source(Expiry("odo", "S3M token", _in(-1)))
    ).run_once()

    agent = ProjectAgent(make_project(tools=["*"]), linked, uuid.uuid4())
    seen: list[str] = []

    def capture(messages, info):
        seen.append(messages[-1].instructions or "")
        return say("ok")

    for channel in ["slack", "app"]:
        with agent.agent.override(model=scripted_model(capture)):
            async for _ in agent.run_stream(user_prompt="hi", channel=channel):
                pass

    assert "Odo: S3M token expired" in seen[0]
    assert "Odo" not in seen[1]


def _jwt(payload) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    return f"e30.{body.decode()}.sig"


def test_a_jwt_exp_is_read_and_anything_else_is_not():
    assert jwt_expiry(_jwt({"exp": 1_800_000_000})) == datetime.fromtimestamp(
        1_800_000_000, UTC
    )
    assert jwt_expiry("an-opaque-token") is None
    assert jwt_expiry(_jwt({"sub": "x"})) is None
    assert jwt_expiry("a.!!!.c") is None
    assert jwt_expiry(None) is None


def test_globus_is_three_days_from_login():
    class U:
        odo_globus_token = None
        odo_globus_connected_at = "2026-10-16T10:00:00+00:00"
        frontier_globus_token = "rt"
        frontier_globus_connected_at = "2026-10-16T10:00:00+00:00"

    (e,) = globus_expiries(U())
    assert e.cluster == "frontier" and e.estimated
    assert e.expires_at == datetime(2026, 10, 19, 10, tzinfo=UTC)


async def test_connecting_globus_stamps_when(session, alice, monkeypatch):
    """The three-day estimate starts from this stamp, set by the login itself."""
    from vista_backend.api.users import GlobusLoginCode, complete_globus_login
    from vista_backend.db.schemas import UserTable
    from vista_backend.services import globus_auth

    monkeypatch.setattr(
        globus_auth,
        "complete_login",
        lambda user_id, cluster, code: globus_auth.Connection(
            refresh_token="rt", https_refresh_token="hrt", identity="a@ornl.gov"
        ),
    )

    await complete_globus_login("frontier", GlobusLoginCode(code="c"), session, alice)

    row = await session.get(UserTable, alice.id)
    assert row is not None and row.frontier_globus_connected_at
    assert row.odo_globus_connected_at is None
