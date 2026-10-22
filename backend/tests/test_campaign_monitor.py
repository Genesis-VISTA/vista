"""Tests for the campaign monitor (poll -> advance -> notify), with poll/collect/email injected."""

import uuid

import pytest

from vista_backend.db.schemas import ProjectTable
from vista_backend.services import campaign as campaign_service
from vista_backend.services import chat_session as chat_session_service
from vista_backend.services import local_settings
from vista_backend.services.campaign_monitor import (
    CampaignMonitor,
    format_job_notification,
    is_success,
    is_terminal,
    normalize_state,
    resume_open_campaigns,
)


async def _make_job(session, *, kind="neutronics", job_id="job-1", notified=False):
    project = ProjectTable(name=f"monitor-{job_id}")
    session.add(project)
    await session.flush()
    chat = await chat_session_service.get_or_create_chat_session(
        session, project_id=project.id
    )
    run = await campaign_service.create_campaign(
        session,
        project_id=project.id,
        session_id=chat.id,
        domain="splash",
        planner_skill="splash-planner",
        title="FLiBe sweep",
    )
    step = await campaign_service.add_step(session, run_id=run.id, cycle=0, kind=kind)
    job = await campaign_service.record_job(
        session,
        job_id=job_id,
        step_id=step.id,
        cluster="frontier",
        job_name=kind,
    )
    if notified:
        await campaign_service.update_job(session, job_id=job_id, notified=True)
    # Notifications go to the address in Settings.
    (await local_settings.get(session)).email = SETTINGS_EMAIL
    await session.flush()
    return run, step, job


SETTINGS_EMAIL = "researcher@example.org"


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
async def test_pending_job_stays_open_and_unnotified(session):
    run, step, job = await _make_job(session)
    emailer = _Emailer()

    async def poll(_session, j):
        return "PENDING", ""

    async def collect(_session, j, raw, ok):
        raise AssertionError("should not collect a pending job")

    monitor = CampaignMonitor(poll=poll, collect=collect, send_email=emailer)
    await monitor.reconcile_once(session)

    refreshed = await campaign_service.get_job(session, job.job_id)
    assert refreshed.state == "PENDING"
    assert refreshed.result_collected is False
    assert refreshed.notified is False
    assert emailer.sent == []
    assert job.job_id in {
        j.job_id for j in await campaign_service.list_open_jobs(session)
    }


@pytest.mark.anyio
async def test_completed_job_is_collected_emailed_and_closed(session):
    run, step, job = await _make_job(session)
    emailer = _Emailer()
    collected = []

    async def poll(_session, j):
        return "COMPLETED", "TBR=1.18"

    async def collect(_session, j, raw, ok):
        collected.append((j.job_id, raw))
        await campaign_service.update_step(
            session,
            step_id=j.step_id,
            status="completed",
            result={"metrics": {"TBR": 1.18}},
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
    assert emailer.sent[0]["to"] == SETTINGS_EMAIL
    assert "COMPLETED" in emailer.sent[0]["subject"]

    assert job.job_id not in {
        j.job_id for j in await campaign_service.list_open_jobs(session)
    }


@pytest.mark.anyio
async def test_a_failure_is_not_believed_on_one_reading(session):
    """
    A scheduler can report FAILED for a job it has not registered yet.

    odo did exactly that: FAILED fifty-five seconds after submission, for a job
    whose own log showed it still building its virtualenv. Because a terminal
    state also stops the watch, no later poll ever corrected it — one wrong
    answer was permanent. So the first sighting only records the state.
    """
    run, step, job = await _make_job(session)
    emailer = _Emailer()

    calls = []

    async def poll(_session, j):
        return "FAILED", "STATE=FAILED"

    # A flag, not a `raise`. `reconcile_once` catches per-job exceptions so one
    # bad job cannot stall the rest, which means an assertion thrown from inside
    # a collector is swallowed and the test passes for the wrong reason.
    async def collect(_session, j, raw, ok):
        calls.append(j.job_id)

    monitor = CampaignMonitor(poll=poll, collect=collect, send_email=emailer)
    await monitor.reconcile_once(session)

    assert calls == [], "a single failure reading must not be acted on"
    refreshed = await campaign_service.get_job(session, job.job_id)
    assert refreshed.state == "FAILED", "the reading is recorded"
    assert refreshed.result_collected is False, "but the job is still watched"
    assert emailer.sent == []
    assert job.job_id in {
        j.job_id for j in await campaign_service.list_open_jobs(session)
    }


@pytest.mark.anyio
async def test_a_failure_the_next_poll_contradicts_is_dropped(session):
    """The whole point: the second reading is allowed to say something else."""
    run, step, job = await _make_job(session)
    emailer = _Emailer()
    answers = iter([("FAILED", ""), ("RUNNING", ""), ("COMPLETED", "TBR=1.18")])
    collected = []

    async def poll(_session, j):
        return next(answers)

    async def collect(_session, j, raw, ok):
        collected.append((raw, ok))
        await campaign_service.update_step(
            session, step_id=j.step_id, status="completed", result={}
        )

    monitor = CampaignMonitor(poll=poll, collect=collect, send_email=emailer)
    for _ in range(3):
        await monitor.reconcile_once(session)

    assert collected == [("TBR=1.18", True)], "it completed; the FAILED was noise"
    refreshed_step = await campaign_service.get_step(session, step.id)
    assert refreshed_step.status == "completed"


@pytest.mark.anyio
async def test_a_confirmed_failure_is_collected_with_its_log(session):
    """
    A failed run goes to the collector too, carrying the status text.

    That text is the scheduler's log, and it is the only account of *why* the job
    died — the difference between "the physics says no" and "the script had a
    typo". Recording the bare state left whoever was waiting with the word
    "failed" and nothing to go on.
    """
    run, step, job = await _make_job(session)
    emailer = _Emailer()
    seen = []

    async def poll(_session, j):
        return "FAILED", "STATE=FAILED\n--- LOGS ---\nsrun: error: node failure"

    async def collect(_session, j, raw, ok):
        seen.append((raw, ok))
        await campaign_service.update_step(
            session, step_id=j.step_id, status="failed", result={"outputs": raw}
        )

    monitor = CampaignMonitor(poll=poll, collect=collect, send_email=emailer)
    await monitor.reconcile_once(session)
    await monitor.reconcile_once(session)

    assert len(seen) == 1
    raw, ok = seen[0]
    assert ok is False
    assert "node failure" in raw

    refreshed_step = await campaign_service.get_step(session, step.id)
    assert refreshed_step.status == "failed"
    assert "node failure" in refreshed_step.result["outputs"]

    refreshed = await campaign_service.get_job(session, job.job_id)
    assert refreshed.result_collected is True
    assert refreshed.notified is True
    assert len(emailer.sent) == 1
    assert "without success" in emailer.sent[0]["body"]


@pytest.mark.anyio
async def test_without_an_email_in_settings_nothing_is_sent_but_the_job_closes(session):
    """
    Settings may name no address. Then there is no one to tell, and the job is
    marked notified all the same, or every poll would try again.
    """
    run, step, job = await _make_job(session)
    (await local_settings.get(session)).email = None
    await session.flush()
    emailer = _Emailer()

    async def poll(_session, j):
        return "COMPLETED", "TBR=1.18"

    async def collect(_session, j, raw, ok):
        pass

    monitor = CampaignMonitor(poll=poll, collect=collect, send_email=emailer)
    await monitor.reconcile_once(session)

    refreshed = await campaign_service.get_job(session, job.job_id)
    assert refreshed.notified is True
    assert emailer.sent == []
    assert job.job_id not in {
        j.job_id for j in await campaign_service.list_open_jobs(session)
    }


@pytest.mark.anyio
async def test_already_notified_job_is_not_reemailed(session):
    run, step, job = await _make_job(session, notified=True)
    emailer = _Emailer()

    async def poll(_session, j):
        return "COMPLETED", ""

    async def collect(_session, j, raw, ok):
        await campaign_service.update_step(
            session, step_id=j.step_id, status="completed", result={}
        )

    monitor = CampaignMonitor(poll=poll, collect=collect, send_email=emailer)
    await monitor.reconcile_once(session)

    assert emailer.sent == []  # notified flag already set; no duplicate email


@pytest.mark.anyio
async def test_monitor_abandons_orphaned_job(session):
    # Multi-session: deleting the conversation backing a campaign sets run.session_id NULL
    # (FK SET NULL), detaching it from its sandbox. The monitor must abandon such jobs, not
    # poll them forever.
    run, step, job = await _make_job(session)
    await campaign_service.update_campaign(session, run_id=run.id, session_id=None)
    emailer = _Emailer()

    async def poll(_session, j):
        raise AssertionError("orphaned job should not be polled")

    async def collect(_session, j, raw, ok):
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
    assert job.job_id not in {
        j.job_id for j in await campaign_service.list_open_jobs(session)
    }


@pytest.mark.anyio
async def test_resume_open_campaigns_lists_non_terminal(session):
    run, step, job = await _make_job(session)
    resumable = await resume_open_campaigns(session)
    assert run.id in {r.id for r in resumable}

    await campaign_service.set_status(session, run_id=run.id, status="exited")
    resumable_after = await resume_open_campaigns(session)
    assert run.id not in {r.id for r in resumable_after}


# --- formatting ------------------------------------------------------------


@pytest.mark.anyio
async def test_format_job_notification(session):
    run, step, job = await _make_job(session)
    subject, body = format_job_notification(
        run=run, job=job, state="COMPLETED", ok=True
    )
    assert "FLiBe sweep" in subject
    assert "completed successfully" in body
    assert job.job_id in body


@pytest.mark.anyio
async def test_a_debate_job_resolves_its_paths_without_a_chat_session(session):
    """
    The bug that made every debate-commissioned job fail on its first poll.

    A debate campaign has no chat session by design — its result goes to a forum
    thread, not a conversation. `_is_orphaned` says exactly that and exempts it.
    The exemption was written there and not in the path resolver, which refused
    with "cannot resolve its sandbox volume" — for a path it derives from the
    project and never from a session.
    """
    from vista_backend.agents.campaign.wiring import _job_context
    from vista_backend.agents.forum.simulation import DEBATE_DOMAIN

    project = ProjectTable(name=f"debate-poll-{uuid.uuid4().hex[:8]}")
    session.add(project)
    await session.flush()
    run = await campaign_service.create_campaign(
        session,
        project_id=project.id,
        session_id=None,
        domain=DEBATE_DOMAIN,
        planner_skill="",
        title="Testing: no shear dependence",
    )
    step = await campaign_service.add_step(
        session, run_id=run.id, cycle=0, kind="simulation"
    )
    job = await campaign_service.record_job(
        session,
        job_id=f"j-{uuid.uuid4().hex[:6]}",
        step_id=step.id,
        cluster="perlmutter",
        job_name="salt-neutronics-tbr",
    )

    _run, _local, paths = await _job_context(session, job)

    assert paths["skills_dir"], "the paths come from the project, not a session"


@pytest.mark.anyio
async def test_a_chat_campaign_that_lost_its_session_is_still_refused(session):
    """
    The guard still does its job for the case it was written for: a conversation
    was deleted, the planner cannot be rebuilt, and polling would achieve nothing.
    """
    from vista_backend.agents.campaign.wiring import _job_context

    _run, _step, job = await _make_job(session, job_id=f"c-{uuid.uuid4().hex[:6]}")
    step = await campaign_service.get_step(session, job.step_id)
    await campaign_service.update_campaign(session, run_id=step.run_id, session_id=None)

    with pytest.raises(ValueError, match="no session_id"):
        await _job_context(session, job)


# --- the model a run was launched on ---------------------------------------


async def _planner_model(session, job, monkeypatch):
    """The `(settings, override)` the monitor's planner rebuild asked a model for."""
    from vista_backend.agents.campaign import agent_tools, wiring

    asked: list[tuple] = []

    def build(local, model=None):
        asked.append((local, model))
        return "built"

    monkeypatch.setattr(agent_tools, "build_model_for", build)
    monkeypatch.setattr(wiring, "load_manifest", lambda _path: object())
    monkeypatch.setattr(wiring, "build_subagents", lambda *a, **kw: {})
    monkeypatch.setattr(wiring, "CampaignPlanner", lambda **kw: kw)
    await wiring.build_planner_for_job(
        session, job, invoke_builder=lambda local, paths: None
    )
    return asked


@pytest.mark.anyio
async def test_a_run_is_rebuilt_on_the_model_it_launched_on(session, monkeypatch):
    """
    Launched on B, the run is rebuilt on B after its chat has moved to C: the
    run carries its own model across the background boundary.
    """
    from vista_backend.db.schemas import ChatSessionUpdate

    run, _step, job = await _make_job(session)
    run.model = "openai:b"
    session.add(run)
    await session.flush()
    await chat_session_service.update_chat_session(
        session,
        project_id=run.project_id,
        chat_session_id=run.session_id,
        updates=ChatSessionUpdate(inference_model="openai:c"),
    )

    asked = await _planner_model(session, job, monkeypatch)

    assert [model for _local, model in asked] == ["openai:b"]


@pytest.mark.anyio
async def test_an_older_run_without_a_model_uses_the_default(session, monkeypatch):
    run, _step, job = await _make_job(session)
    assert run.model is None

    asked = await _planner_model(session, job, monkeypatch)

    assert [model for _local, model in asked] == [None]


async def _launched_on_olcf(session, *, now_on):
    """A run launched on OLCF's `gpt-oss-120b`, with Settings now on `now_on`."""
    run, _step, job = await _make_job(session)
    run.model, run.provider = "openai:gpt-oss-120b", "olcf"
    session.add(run)
    (await local_settings.get(session)).inference_provider = now_on
    await session.flush()
    return job


@pytest.mark.anyio
async def test_a_run_keeps_its_model_while_settings_stay_on_that_provider(
    session, monkeypatch
):
    job = await _launched_on_olcf(session, now_on="olcf")

    asked = await _planner_model(session, job, monkeypatch)

    assert [model for _local, model in asked] == ["openai:gpt-oss-120b"]


@pytest.mark.anyio
async def test_after_a_provider_switch_a_run_follows_the_default(session, monkeypatch):
    """OLCF's model name means nothing to i2, so the run moves to i2's default."""
    job = await _launched_on_olcf(session, now_on="i2")

    asked = await _planner_model(session, job, monkeypatch)

    assert [model for _local, model in asked] == [None]
