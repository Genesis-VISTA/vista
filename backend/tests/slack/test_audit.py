"""The Slack audit log: who asked, who confirmed, what ran, and never a credential."""

import json

import pytest
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.db.schemas import SlackAuditTable
from vista_backend.services import chat_run
from vista_backend.slack import audit
from vista_backend.slack.audit import REDACTED, redact
from vista_backend.slack.confirm import CANCEL

from .fakes import FakeTransport
from .test_confirm import _chat, _offer, _tap
from .test_job_refs import SUMMARY, _submit, _watcher

pytestmark = pytest.mark.anyio


@pytest.fixture
def jsonl(tmp_path, monkeypatch):
    path = tmp_path / "logs" / "slack-audit.jsonl"
    monkeypatch.setattr(audit, "log_path", lambda: path)
    return path


@pytest.fixture
def answers(engine, monkeypatch):
    monkeypatch.setattr(chat_run, "session_factory", lambda: AsyncSession(engine))

    async def resolve(elicitation_id, action, content=None):
        return True

    monkeypatch.setattr(chat_run, "resolve_prompt", resolve)


async def _rows(engine) -> list[SlackAuditTable]:
    async with AsyncSession(engine) as s:
        query = select(SlackAuditTable).order_by(col(SlackAuditTable.at))
        return list((await s.exec(query)).all())


def test_redact_drops_credential_keys_and_masks_tokens():
    value = {
        "job": "flibe-md",
        "s3m_token": "abc",
        "nested": {"Password": "hunter2", "n": 2, "ok": True},
        "note": "use xoxb-123-456 or Bearer abc.def and eyJa.eyJb.sig",
        "list": [{"passcode": "123456"}, "xapp-1-A"],
    }

    out = redact(value)

    assert out["job"] == "flibe-md"
    assert out["s3m_token"] == REDACTED
    assert out["nested"] == {"Password": REDACTED, "n": 2, "ok": True}
    assert "xoxb" not in out["note"] and "abc.def" not in out["note"]
    assert "eyJ" not in out["note"]
    assert out["list"] == [{"passcode": REDACTED}, REDACTED]


async def test_a_confirmed_submission_is_one_trail(
    answers, jsonl, engine, session, alice
):
    """Request, card, confirmation by the requester, and the job id it got."""
    slack = FakeTransport()
    confirmations = await _offer(slack, session, alice)
    chat_id = (await _rows(engine))[0].chat_session_id
    await audit.request(
        team_id="T1",
        slack_user_id="U1",
        user_id=alice.id,
        chat_session_id=chat_id,
        channel_id="DU1",
        thread_ts="10.0",
        text="submit flibe-md, my token is xoxb-999-abc",
    )

    await confirmations.on_action(_tap())
    assert chat_id is not None
    await _submit(_watcher(engine, alice, chat_id, channel="slack"), SUMMARY)

    rows = await _rows(engine)
    events = [r.event for r in rows]
    assert sorted(events) == sorted(
        ["confirm_posted", "request", "confirmed", "tool_result"]
    )
    assert all(r.user_id == alice.id for r in rows)
    confirmed = next(r for r in rows if r.event == "confirmed")
    assert confirmed.slack_user_id == "U1" and confirmed.detail
    assert confirmed.detail["by"] == "U1"
    result = next(r for r in rows if r.event == "tool_result")
    assert result.job_id == "4471" and result.tool_name == "submit_hpc_job"

    lines = [
        json.loads(line)
        for line in jsonl.read_text(encoding="utf-8").split("\n")
        if line
    ]
    assert [line["event"] for line in lines] == events
    everything = jsonl.read_text(encoding="utf-8") + json.dumps(
        [r.detail for r in rows]
    )
    assert "xoxb-999" not in everything
    confirmations.stop()


async def test_cancel_and_expiry_are_recorded(answers, jsonl, engine, session, alice):
    slack = FakeTransport()
    confirmations = await _offer(slack, session, alice)
    await confirmations.on_action(_tap(CANCEL))
    second = await _offer(slack, session, alice, eid="e2")
    await second.expire("e2")

    events = [r.event for r in await _rows(engine)]
    assert events.count("cancelled") == 1 and events.count("expired") == 1
    confirmations.stop()
    second.stop()


async def test_an_app_submit_is_not_audited(answers, jsonl, engine, session, alice):
    chat_id = await _chat(session, alice)

    await _submit(_watcher(engine, alice, chat_id, channel="app"), SUMMARY)

    assert await _rows(engine) == []
    assert not jsonl.exists()


async def test_a_failed_write_does_not_stop_the_action(answers, engine, monkeypatch):
    def unwritable():
        raise OSError("read-only")

    monkeypatch.setattr(audit, "log_path", unwritable)

    await audit.record("refused", detail={"reason": "locked"})  # No exception.
