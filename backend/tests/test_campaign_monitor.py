"""Tests for the campaign monitor (poll -> advance -> notify), with poll/collect/email injected."""
import pytest

from vista_backend.db.schemas import ProjectTable
from vista_backend.services import campaign as campaign_service
from vista_backend.services import chat_session as chat_session_service
from vista_backend.services.campaign_monitor import (
    CampaignMonitor,
    format_job_notification,
    is_success,
    is_terminal,
    normalize_state,
    resume_open_campaigns,
)


async def _make_job(session, alice, *, kind="neutronics", job_id="job-1", notified=False):
    project = ProjectTable(name=f"monitor-{job_id}")
    session.add(project)
    await session.flush()
    chat = await chat_session_service.get_or_create_chat_session(
        session, project_id=project.id, user_id=alice.id
    )
    run = await campaign_service.create_campaign(
        session, project_id=project.id, user_id=alice.id, session_id=chat.id,
        domain="splash", planner_skill="splash-planner", title="FLiBe sweep",
    )
    step = await campaign_service.add_step(session, run_id=run.id, cycle=0, kind=kind)
    job = await campaign_service.record_job(
        session, job_id=job_id, step_id=step.id, user_id=alice.id,
        cluster="frontier", job_name=kind,
    )
    if notified:
        await campaign_service.update_job(session, job_id=job_id, notified=True)
    return run, step, job


class _Emailer:
    def __init__(self):
        self.sent = []

    async def __call__(self, *, to, subject, body):
        self.sent.append({"to": to, "subject": subject, "body": body})
        return True


# --- classification --------------------------------------------------------

def test_state_classification():
    assert normalize_state(" completed ") == "COMPLETED"
    assert is_terminal("COMPLETED") and is_success("COMPLETED")
    assert is_terminal("FAILED") and not is_success("FAILED")
    assert is_terminal("cancelled") and not is_success("cancelled")
    assert not is_terminal("RUNNING")
    assert not is_terminal("PENDING")


# --- reconcile -------------------------------------------------------------

@pytest.mark.anyio
async def test_pending_job_stays_open_and_unnotified(session, alice):
    run, step, job = await _make_job(session, alice)
    emailer = _Emailer()

    async def poll(_session, j):
        return "PENDING", ""

    async def collect(_session, j, raw):
        raise AssertionError("should not collect a pending job")

    monitor = CampaignMonitor(poll=poll, collect=collect, send_email=emailer)
    await monitor.reconcile_once(session)

    refreshed = await campaign_service.get_job(session, job.job_id)
    assert refreshed.state == "PENDING"
    assert refreshed.result_collected is False
    assert refreshed.notified is False
    assert emailer.sent == []
    assert job.job_id in {j.job_id for j in await campaign_service.list_open_jobs(session)}


@pytest.mark.anyio
async def test_completed_job_is_collected_emailed_and_closed(session, alice):
    run, step, job = await _make_job(session, alice)
    emailer = _Emailer()
    collected = []

    async def poll(_session, j):
        return "COMPLETED", "TBR=1.18"

    async def collect(_session, j, raw):
        collected.append((j.job_id, raw))
        await campaign_service.update_step(
            session, step_id=j.step_id, status="completed", result={"metrics": {"TBR": 1.18}}
        )

    monitor = CampaignMonitor(poll=poll, collect=collect, send_email=emailer)
    await monitor.reconcile_once(session)

    assert collected == [(job.job_id, "TBR=1.18")]

    refreshed = await campaign_service.get_job(session, job.job_id)
    assert refreshed.state == "COMPLETED"
    assert refreshed.result_collected is True
    assert refreshed.notified is True
    assert refreshed.last_polled_at

    refreshed_step = await campaign_service.get_step(session, step.id)
    assert refreshed_step.status == "completed"

    assert len(emailer.sent) == 1
    assert emailer.sent[0]["to"] == (await _user_email(session, alice))
    assert "COMPLETED" in emailer.sent[0]["subject"]

    assert job.job_id not in {j.job_id for j in await campaign_service.list_open_jobs(session)}


@pytest.mark.anyio
async def test_failed_job_marks_step_failed_and_emails(session, alice):
    run, step, job = await _make_job(session, alice)
    emailer = _Emailer()

    async def poll(_session, j):
        return "FAILED", ""

    async def collect(_session, j, raw):
        raise AssertionError("failed jobs should not be collected")

    monitor = CampaignMonitor(poll=poll, collect=collect, send_email=emailer)
    await monitor.reconcile_once(session)

    refreshed_step = await campaign_service.get_step(session, step.id)
    assert refreshed_step.status == "failed"
    assert refreshed_step.result["state"] == "FAILED"

    refreshed = await campaign_service.get_job(session, job.job_id)
    assert refreshed.result_collected is True
    assert refreshed.notified is True
    assert len(emailer.sent) == 1
    assert "without success" in emailer.sent[0]["body"]


@pytest.mark.anyio
async def test_already_notified_job_is_not_reemailed(session, alice):
    run, step, job = await _make_job(session, alice, notified=True)
    emailer = _Emailer()

    async def poll(_session, j):
        return "COMPLETED", ""

    async def collect(_session, j, raw):
        await campaign_service.update_step(session, step_id=j.step_id, status="completed", result={})

    monitor = CampaignMonitor(poll=poll, collect=collect, send_email=emailer)
    await monitor.reconcile_once(session)

    assert emailer.sent == []  # notified flag already set; no duplicate email


@pytest.mark.anyio
async def test_monitor_abandons_orphaned_job(session, alice):
    # Multi-session: deleting the conversation backing a campaign sets run.session_id NULL
    # (FK SET NULL), detaching it from its sandbox. The monitor must abandon such jobs, not
    # poll them forever.
    run, step, job = await _make_job(session, alice)
    await campaign_service.update_campaign(session, run_id=run.id, session_id=None)
    emailer = _Emailer()

    async def poll(_session, j):
        raise AssertionError("orphaned job should not be polled")

    async def collect(_session, j, raw):
        raise AssertionError("orphaned job should not be collected")

    monitor = CampaignMonitor(poll=poll, collect=collect, send_email=emailer)
    await monitor.reconcile_once(session)

    refreshed_step = await campaign_service.get_step(session, step.id)
    assert refreshed_step.status == "failed"
    assert "abandoned" in refreshed_step.result
    refreshed_job = await campaign_service.get_job(session, job.job_id)
    assert refreshed_job.result_collected is True
    assert emailer.sent == []
    # No longer in the open set, so it won't be polled again.
    assert job.job_id not in {j.job_id for j in await campaign_service.list_open_jobs(session)}


@pytest.mark.anyio
async def test_resume_open_campaigns_lists_non_terminal(session, alice):
    run, step, job = await _make_job(session, alice)
    resumable = await resume_open_campaigns(session)
    assert run.id in {r.id for r in resumable}

    await campaign_service.set_status(session, run_id=run.id, status="exited")
    resumable_after = await resume_open_campaigns(session)
    assert run.id not in {r.id for r in resumable_after}


# --- formatting ------------------------------------------------------------

@pytest.mark.anyio
async def test_format_job_notification(session, alice):
    run, step, job = await _make_job(session, alice)
    subject, body = format_job_notification(run=run, job=job, state="COMPLETED", ok=True)
    assert "FLiBe sweep" in subject
    assert "completed successfully" in body
    assert job.job_id in body


async def _user_email(session, alice):
    from vista_backend.db.schemas import UserTable

    user = await session.get(UserTable, alice.id)
    return user.email
