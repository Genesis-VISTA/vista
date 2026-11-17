"""The lock and the limits on Slack jobs, set only from the desktop app."""

import uuid
from datetime import timedelta

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.db.schemas import HpcJobRecordTable, SlackConfigTable
from vista_backend.services import chat_run
from vista_backend.slack import confirm, limits, store
from vista_backend.slack.limits import LOCKED, check, minutes, refusal

pytestmark = pytest.mark.anyio

SUBMIT = "vista__submit_hpc_job"


def _config(**kw) -> SlackConfigTable:
    return SlackConfigTable(
        id=1,
        allowed_team_ids=[],
        allowed_channel_ids=[],
        **{"max_nodes": 4, "max_walltime_minutes": 120, "max_jobs_per_day": 2, **kw},
    )


@pytest.fixture
def db(engine, monkeypatch):
    monkeypatch.setattr(chat_run, "session_factory", lambda: AsyncSession(engine))
    return engine


async def _jobs(session, n, *, origin="slack", hours_ago=1):
    for i in range(n):
        session.add(
            HpcJobRecordTable(
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


async def test_locked_refuses_every_side_effect(db):
    config = _config(locked=True)

    assert await check(config, SUBMIT, {}) == LOCKED
    assert await check(config, "vista__cancel_hpc_job", {}) == LOCKED
    assert await check(config, "run_bash", {}) == LOCKED


async def test_within_limits_passes(db):
    assert (
        await check(_config(), SUBMIT, {"node_count": 4, "duration": "2:00:00"}) is None
    )


async def test_over_the_node_limit_names_it(db):
    why = await check(_config(), SUBMIT, {"node_count": 8})

    assert why is not None and "4-node limit" in why


async def test_over_the_walltime_limit_names_it(db):
    why = await check(_config(), SUBMIT, {"duration": "2:00:01"})

    assert why is not None and "120-minute" in why


async def test_the_daily_limit_counts_slack_jobs_only(db, session):
    await _jobs(session, 2, origin="app")
    await _jobs(session, 1, hours_ago=30)  # Yesterday's, outside the window.
    await _jobs(session, 1)
    assert await check(_config(), SUBMIT, {}) is None

    await _jobs(session, 1)
    why = await check(_config(), SUBMIT, {})

    assert why is not None and "limit is 2" in why
    assert "<!date^" in why, "says when the count resets, in the reader's time zone"


async def test_limits_apply_to_submissions_only(db):
    config = _config(max_nodes=1)

    assert await check(config, "vista__cancel_hpc_job", {"node_count": 9}) is None


async def test_refusal_reads_the_saved_settings(db, session):
    row = await store.get_config(session)
    row.max_nodes = 2
    session.add(row)
    await session.commit()

    why = await refusal(SUBMIT, {"node_count": 3})

    assert why is not None and "2-node limit" in why


async def test_lock_from_slack_holds_until_the_desktop(db, engine):
    await limits.lock("U1")
    await limits.lock("U1")  # Again: still locked, by the first.

    async with AsyncSession(engine) as s:
        row = await s.get(SlackConfigTable, 1)
        assert row is not None
        assert row.locked and row.locked_by == "slack:U1" and row.locked_at
    assert await refusal(SUBMIT, {}) == LOCKED


# ---------------------------------------------------------------------------
# Group 5: the job as it would be submitted
# ---------------------------------------------------------------------------


async def test_a_default_over_the_limit_is_refused(db, slack_config):
    """The model left nodes and walltime out; the job's defaults are over the limits."""
    config = SlackConfigTable.model_validate(
        {**slack_config.model_dump(), "max_nodes": 4}
    )
    preview = {"Nodes": "16", "Walltime": "1:00:00 (3600s)"}
    why = await limits.check(
        config, "vista__submit_hpc_job", {"job": "flibe-md"}, preview
    )
    assert why is not None and "16 nodes" in why

    long = {"Nodes": "1", "Walltime": "12:00:00 (43200s)"}
    why = await limits.check(config, "vista__submit_hpc_job", {"job": "flibe-md"}, long)
    assert why is not None and "12:00:00" in why


async def test_the_preview_wins_over_the_models_arguments(db, slack_config):
    config = SlackConfigTable.model_validate(
        {**slack_config.model_dump(), "max_nodes": 4}
    )
    preview = {"Nodes": "2", "Walltime": "0:30:00 (1800s)"}
    args = {"job": "flibe-md", "node_count": 2}
    assert await limits.check(config, "vista__submit_hpc_job", args, preview) is None


async def test_refusal_previews_the_conversations_job(
    db, session, slack_config, monkeypatch
):
    row = await store.get_config(session)
    row.max_nodes = 4
    session.add(row)
    await session.commit()
    asked = []

    async def preview(chat_session_id, tool, args):
        asked.append(chat_session_id)
        return {"Nodes": "16", "Walltime": "1:00:00 (3600s)"}

    monkeypatch.setattr(confirm, "_preview", preview)
    chat = uuid.uuid4()
    why = await limits.refusal(
        "vista__submit_hpc_job", {"job": "flibe-md"}, chat_session_id=chat
    )
    assert asked == [chat] and why is not None and "16 nodes" in why


async def test_an_agenthpc_submission_counts_toward_the_daily_limit(
    db, session, slack_config
):
    config = SlackConfigTable.model_validate(
        {**slack_config.model_dump(), "max_jobs_per_day": 2}
    )
    await _jobs(session, 2)
    why = await limits.check(
        config, "vista__agenthpc_submit_parameter_set", {"app_type": "monbtaw"}
    )
    assert why is not None and "2 jobs" in why
    assert (
        await limits.check(
            config, "vista__agenthpc_cancel_job", {"app_type": "monbtaw"}
        )
        is None
    ), "a cancellation is never over a limit"
