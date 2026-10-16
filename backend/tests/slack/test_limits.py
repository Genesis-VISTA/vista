"""The lock and the limits on Slack jobs, set only from the desktop app."""

import uuid
from datetime import timedelta

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.db.schemas import HpcJobRecordTable, SlackConfigTable
from vista_backend.services import chat_run
from vista_backend.slack import limits, store
from vista_backend.slack.limits import LOCKED, check, minutes, refusal

pytestmark = pytest.mark.anyio

SUBMIT = "vista__submit_hpc_job"


def _config(**kw) -> SlackConfigTable:
    return SlackConfigTable(
        id=1,
        allowed_team_ids=[],
        allowed_channel_ids=[],
        lease_status=None,
        **{"max_nodes": 4, "max_walltime_minutes": 120, "max_jobs_per_day": 2, **kw},
    )


@pytest.fixture
def db(engine, monkeypatch):
    monkeypatch.setattr(chat_run, "session_factory", lambda: AsyncSession(engine))
    return engine


async def _jobs(session, user_id, n, *, origin="slack", hours_ago=1):
    for i in range(n):
        session.add(
            HpcJobRecordTable(
                user_id=user_id,
                origin=origin,
                cluster="frontier",
                job_id=uuid.uuid4().hex,
                job="flibe-md",
                submitted_at=(store.now() - timedelta(hours=hours_ago)).isoformat(
                    timespec="seconds"
                ),
            )
        )
    await session.commit()


def test_minutes():
    assert minutes("2:00:00") == 120
    assert minutes("0:00:01") == 1
    assert minutes("1:30:30") == 91
    assert minutes("soon") is None


async def test_locked_refuses_every_side_effect(db, alice):
    config = _config(locked=True)

    assert await check(config, alice.id, SUBMIT, {}) == LOCKED
    assert await check(config, alice.id, "vista__cancel_hpc_job", {}) == LOCKED
    assert await check(config, alice.id, "run_bash", {}) == LOCKED


async def test_within_limits_passes(db, alice):
    assert (
        await check(
            _config(), alice.id, SUBMIT, {"node_count": 4, "duration": "2:00:00"}
        )
        is None
    )


async def test_over_the_node_limit_names_it(db, alice):
    why = await check(_config(), alice.id, SUBMIT, {"node_count": 8})

    assert why is not None and "4-node limit" in why


async def test_over_the_walltime_limit_names_it(db, alice):
    why = await check(_config(), alice.id, SUBMIT, {"duration": "2:00:01"})

    assert why is not None and "120-minute" in why


async def test_the_daily_limit_counts_slack_jobs_only(db, session, alice, bob):
    await _jobs(session, alice.id, 2, origin="app")
    await _jobs(session, alice.id, 1, hours_ago=30)  # Yesterday's, outside the window.
    await _jobs(session, bob.id, 2)
    assert await check(_config(), alice.id, SUBMIT, {}) is None

    await _jobs(session, alice.id, 2)
    why = await check(_config(), alice.id, SUBMIT, {})

    assert why is not None and "limit is 2" in why
    assert "<!date^" in why, "says when the count resets, in the reader's time zone"


async def test_limits_apply_to_submissions_only(db, alice):
    config = _config(max_nodes=1)

    assert (
        await check(config, alice.id, "vista__cancel_hpc_job", {"node_count": 9})
        is None
    )


async def test_refusal_reads_the_saved_settings(db, session, alice):
    row = await store.get_config(session)
    row.max_nodes = 2
    session.add(row)
    await session.commit()

    why = await refusal(alice.id, SUBMIT, {"node_count": 3})

    assert why is not None and "2-node limit" in why


async def test_lock_from_slack_holds_until_the_desktop(db, engine):
    await limits.lock("U1")
    await limits.lock("U1")  # Again: still locked, by the first.

    async with AsyncSession(engine) as s:
        row = await s.get(SlackConfigTable, 1)
        assert row is not None
        assert row.locked and row.locked_by == "slack:U1" and row.locked_at
    assert await refusal(uuid.uuid4(), SUBMIT, {}) == LOCKED
