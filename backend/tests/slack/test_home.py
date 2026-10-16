"""App Home: status, host, credentials, lock and recent jobs."""

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.db.schemas import HpcJobRecordTable, SlackConfigTable
from vista_backend.services import chat_run
from vista_backend.slack import credentials, home, store
from vista_backend.slack.credentials import Expiry
from vista_backend.slack.home import Home, view

from .conftest import TEAM, link
from .fakes import FakeTransport

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def _config(**kw) -> SlackConfigTable:
    return SlackConfigTable(
        id=1,
        allowed_team_ids=[],
        allowed_channel_ids=[],
        online_since="2026-10-16T08:00:00+00:00",
        **kw,
    )


def _job(**kw) -> HpcJobRecordTable:
    return HpcJobRecordTable(
        user_id=uuid.uuid4(),
        cluster="frontier",
        job_id="4471",
        job="flibe-md",
        submitted_at="2026-10-16T09:00:00+00:00",
        **kw,
    )


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(store, "now", lambda: NOW)


def test_the_home_view_snapshot():
    blocks = view(
        online=True,
        host="lab-mac",
        config=_config(),
        expiries=[
            Expiry("frontier", "S3M token", datetime(2026, 10, 17, 9, tzinfo=UTC)),
            Expiry(
                "frontier", "Globus session", datetime(2026, 10, 18, tzinfo=UTC), True
            ),
        ],
        jobs=[_job(state="COMPLETED")],
        as_of="2026-10-16T12:00:00+00:00",
    )

    assert blocks == {
        "type": "home",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "VISTA"}},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":large_green_circle: *Online* on `lab-mac` since "
                    "<!date^1792137600^{date_short_pretty} {time}|2026-10-16T08:00:00+00:00>",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":unlock: Not locked. Send `lock` to block changes from Slack.",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*HPC access*\n"
                    "• Frontier S3M token: expires "
                    "<!date^1792227600^{date_short_pretty} {time}|2026-10-17T09:00:00+00:00>\n"
                    "• Frontier Globus session: expires "
                    "<!date^1792281600^{date_short_pretty} {time}|2026-10-18T00:00:00+00:00>"
                    " (estimated)",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Recent jobs from Slack*\n"
                    "• `4471` flibe-md on frontier: COMPLETED, "
                    "<!date^1792141200^{date_short_pretty} {time}|2026-10-16T09:00:00+00:00>",
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "Slack limits: 4 nodes, 120 min walltime, 5 jobs a "
                        "day. Set in the desktop app. As of "
                        "<!date^1792152000^{date_short_pretty} {time}|2026-10-16T12:00:00+00:00>.",
                    }
                ],
            },
        ],
    }


def test_offline_locked_and_expired():
    text = json.dumps(
        view(
            online=False,
            host="lab-mac",
            config=_config(locked=True, locked_at="2026-10-16T11:00:00+00:00"),
            expiries=[Expiry("odo", "S3M token", datetime(2026, 10, 16, tzinfo=UTC))],
            jobs=[],
            as_of="2026-10-16T12:00:00+00:00",
        )
    )

    assert "*Offline*" in text and "won't be received" in text
    assert "*Locked*" in text
    assert "Odo S3M token: expired" in text
    assert "None yet." in text


async def test_publish_for_a_linked_user_only(engine, session, alice, monkeypatch):
    monkeypatch.setattr(chat_run, "session_factory", lambda: AsyncSession(engine))
    monkeypatch.setattr(credentials, "_known", {})
    await link(session, alice, "U1")
    session.add(
        HpcJobRecordTable(
            user_id=alice.id,
            origin="slack",
            cluster="lux",
            job_id="9",
            job="lux-hello",
            submitted_at="2026-10-16T09:00:00+00:00",
        )
    )
    await session.commit()
    slack = FakeTransport()
    tab = Home(slack, host="lab-mac")

    assert await tab.publish(TEAM, "U1")
    assert not await tab.publish(TEAM, "U2")
    assert await tab.publish_all(online=False) == 1

    assert list(slack.homes) == ["U1"]
    text = json.dumps(slack.homes["U1"])
    assert "lux-hello" in text and "*Offline*" in text
    assert home.RECENT == 5
