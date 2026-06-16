"""Tests for the campaign service (run/step/job lifecycle over the campaign tables)."""
import pytest

from vista_backend.db.schemas import ProjectTable
from vista_backend.services import campaign as campaign_service


async def _make_project(session) -> ProjectTable:
    project = ProjectTable(name="svc-campaign-project")
    session.add(project)
    await session.flush()
    return project


async def _make_campaign(session, alice, **overrides):
    project = await _make_project(session)
    return await campaign_service.create_campaign(
        session,
        project_id=project.id,
        user_id=alice.id,
        domain="splash",
        planner_skill="splash-planner",
        **overrides,
    )


@pytest.mark.anyio
async def test_create_campaign_defaults_to_gathering(session, alice):
    run = await _make_campaign(session, alice, title="FLiBe sweep")
    assert run.status == "gathering"
    assert run.title == "FLiBe sweep"
    assert run.spec == {}
    assert run.plan == []
    assert run.created_at and run.updated_at


@pytest.mark.anyio
async def test_get_and_require_campaign(session, alice):
    run = await _make_campaign(session, alice)
    assert (await campaign_service.get_campaign(session, run.id)).id == run.id
    assert (await campaign_service.require_campaign(session, run.id)).id == run.id


@pytest.mark.anyio
async def test_require_campaign_raises_when_missing(session):
    import uuid

    with pytest.raises(ValueError):
        await campaign_service.require_campaign(session, uuid.uuid4())


@pytest.mark.anyio
async def test_list_campaigns_filters(session, alice, bob):
    run_a = await _make_campaign(session, alice)
    # A second campaign owned by bob in a different project.
    project_b = ProjectTable(name="bob-project")
    session.add(project_b)
    await session.flush()
    run_b = await campaign_service.create_campaign(
        session,
        project_id=project_b.id,
        user_id=bob.id,
        domain="splash",
        planner_skill="splash-planner",
    )

    by_user = await campaign_service.list_campaigns(session, user_id=alice.id)
    assert [r.id for r in by_user] == [run_a.id]

    all_runs = await campaign_service.list_campaigns(session)
    assert {r.id for r in all_runs} == {run_a.id, run_b.id}


@pytest.mark.anyio
async def test_status_transition_and_resumable_filter(session, alice):
    run = await _make_campaign(session, alice)

    updated = await campaign_service.set_status(session, run_id=run.id, status="running")
    assert updated.status == "running"
    assert updated.updated_at >= run.created_at

    resumable = await campaign_service.list_resumable_campaigns(session)
    assert run.id in {r.id for r in resumable}

    # Terminal status drops it from the resumable set.
    await campaign_service.set_status(session, run_id=run.id, status="exited")
    resumable_after = await campaign_service.list_resumable_campaigns(session)
    assert run.id not in {r.id for r in resumable_after}


@pytest.mark.anyio
async def test_save_plan_and_update_spec(session, alice):
    run = await _make_campaign(session, alice)
    await campaign_service.save_plan(
        session, run_id=run.id, plan=[{"step": 1, "text": "grid sweep"}]
    )
    await campaign_service.update_campaign(
        session, run_id=run.id, spec={"platform": "frontier", "tbr_target": 1.1}
    )
    reloaded = await campaign_service.require_campaign(session, run.id)
    assert reloaded.plan[0]["text"] == "grid sweep"
    assert reloaded.spec["tbr_target"] == 1.1


@pytest.mark.anyio
async def test_steps_create_list_and_update(session, alice):
    run = await _make_campaign(session, alice)
    neutronics = await campaign_service.add_step(
        session, run_id=run.id, cycle=0, kind="neutronics", candidate={"li6": 0.7}
    )
    await campaign_service.add_step(session, run_id=run.id, cycle=0, kind="chemistry")

    steps = await campaign_service.list_steps(session, run_id=run.id, cycle=0)
    assert {s.kind for s in steps} == {"neutronics", "chemistry"}
    assert all(s.status == "pending" for s in steps)

    completed = await campaign_service.update_step(
        session, step_id=neutronics.id, status="completed", result={"TBR": 1.18}
    )
    assert completed.status == "completed"
    assert completed.result["TBR"] == 1.18


@pytest.mark.anyio
async def test_update_step_raises_when_missing(session):
    import uuid

    with pytest.raises(ValueError):
        await campaign_service.update_step(session, step_id=uuid.uuid4(), status="completed")


@pytest.mark.anyio
async def test_record_job_and_open_jobs_lifecycle(session, alice):
    run = await _make_campaign(session, alice)
    step = await campaign_service.add_step(session, run_id=run.id, cycle=0, kind="neutronics")

    job = await campaign_service.record_job(
        session,
        job_id="job-100",
        step_id=step.id,
        user_id=alice.id,
        cluster="frontier",
        job_name="neutronics",
        log_path="/o/log-100.out",
        output_dir="/o/100",
    )
    assert job.state == "submitted"
    assert job.result_collected is False

    for_step = await campaign_service.list_jobs_for_step(session, step_id=step.id)
    assert [j.job_id for j in for_step] == ["job-100"]

    # Open until its outputs are collected.
    assert "job-100" in {j.job_id for j in await campaign_service.list_open_jobs(session)}

    await campaign_service.update_job(
        session,
        job_id="job-100",
        state="COMPLETED",
        last_polled_at="2026-06-16T00:00:00+00:00",
        notified=True,
        result_collected=True,
    )
    open_after = await campaign_service.list_open_jobs(session)
    assert "job-100" not in {j.job_id for j in open_after}

    reloaded = await campaign_service.get_job(session, "job-100")
    assert reloaded.state == "COMPLETED"
    assert reloaded.notified is True


@pytest.mark.anyio
async def test_update_job_raises_when_missing(session):
    with pytest.raises(ValueError):
        await campaign_service.update_job(session, job_id="nope", state="COMPLETED")
