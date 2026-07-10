"""CRUD + relationship tests for the campaign data model (CampaignRun / CampaignStep / HpcJob)."""

import pytest
from sqlmodel import select

from vista_backend.db.schemas import (
    CampaignRunTable,
    CampaignStepTable,
    HpcJobTable,
    ProjectTable,
)
from vista_backend.utils.misc import now_iso


async def _make_project(session) -> ProjectTable:
    project = ProjectTable(name="campaign-project")
    session.add(project)
    await session.flush()
    return project


async def _make_run(session, project, user, **overrides) -> CampaignRunTable:
    now = now_iso()
    run = CampaignRunTable(
        domain="splash",
        planner_skill="splash-planner",
        project_id=project.id,
        user_id=user.id,
        created_at=now,
        updated_at=now,
        **overrides,
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


@pytest.mark.anyio
async def test_create_and_read_campaign_run(session, alice):
    project = await _make_project(session)
    run = await _make_run(
        session,
        project,
        alice,
        title="FLiBe TBR sweep",
        spec={"variables": {"li6_enrichment": [0.5, 0.9]}, "platform": "frontier"},
        plan=[{"step": 1, "text": "neutronics + chemistry on grid"}],
    )

    reloaded = (
        await session.exec(
            select(CampaignRunTable).where(CampaignRunTable.id == run.id)
        )
    ).one()

    assert reloaded.domain == "splash"
    assert reloaded.planner_skill == "splash-planner"
    assert reloaded.project_id == project.id
    assert reloaded.user_id == alice.id
    assert reloaded.session_id is None
    # JSON columns round-trip as native dict/list.
    assert reloaded.spec["platform"] == "frontier"
    assert reloaded.plan[0]["step"] == 1
    # Defaults.
    assert reloaded.status == "gathering"


@pytest.mark.anyio
async def test_campaign_run_defaults_empty_spec_and_plan(session, alice):
    project = await _make_project(session)
    run = await _make_run(session, project, alice)
    assert run.spec == {}
    assert run.plan == []


@pytest.mark.anyio
async def test_campaign_status_transition(session, alice):
    project = await _make_project(session)
    run = await _make_run(session, project, alice)

    run.status = "running"
    run.updated_at = now_iso()
    session.add(run)
    await session.flush()

    reloaded = (
        await session.exec(
            select(CampaignRunTable).where(CampaignRunTable.id == run.id)
        )
    ).one()
    assert reloaded.status == "running"


@pytest.mark.anyio
async def test_steps_linked_to_run_and_queryable_by_cycle(session, alice):
    project = await _make_project(session)
    run = await _make_run(session, project, alice)

    for cycle, kind in [(0, "neutronics"), (0, "chemistry"), (1, "decision")]:
        session.add(
            CampaignStepTable(
                run_id=run.id,
                cycle=cycle,
                kind=kind,
                candidate=None if kind == "decision" else {"li6": 0.7},
                order_spec={"platform": "frontier"},
                created_at=now_iso(),
                updated_at=now_iso(),
            )
        )
    await session.flush()

    steps = (
        await session.exec(
            select(CampaignStepTable)
            .where(CampaignStepTable.run_id == run.id)
            .order_by(CampaignStepTable.cycle, CampaignStepTable.kind)
        )
    ).all()

    assert [s.kind for s in steps] == ["chemistry", "neutronics", "decision"]
    assert all(s.status == "pending" for s in steps)
    # candidate JSON round-trips; decision step has none.
    neutronics = next(s for s in steps if s.kind == "neutronics")
    assert neutronics.candidate == {"li6": 0.7}
    decision = next(s for s in steps if s.kind == "decision")
    assert decision.candidate is None


@pytest.mark.anyio
async def test_hpc_job_linked_to_step(session, alice):
    project = await _make_project(session)
    run = await _make_run(session, project, alice)
    step = CampaignStepTable(
        run_id=run.id,
        cycle=0,
        kind="neutronics",
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    session.add(step)
    await session.flush()

    job = HpcJobTable(
        job_id="123456",
        step_id=step.id,
        user_id=alice.id,
        cluster="frontier",
        job_name="neutronics",
        log_path="/remote/out/log-123456.out",
        output_dir="/remote/out/123456",
        submitted_at=now_iso(),
    )
    session.add(job)
    await session.flush()

    reloaded = (
        await session.exec(select(HpcJobTable).where(HpcJobTable.job_id == "123456"))
    ).one()
    assert reloaded.step_id == step.id
    assert reloaded.user_id == alice.id
    assert reloaded.cluster == "frontier"
    assert reloaded.state == "submitted"
    assert reloaded.notified is False
    assert reloaded.result_collected is False


@pytest.mark.anyio
async def test_cascade_delete_run_removes_steps_and_jobs(session, alice):
    project = await _make_project(session)
    run = await _make_run(session, project, alice)
    step = CampaignStepTable(
        run_id=run.id,
        cycle=0,
        kind="neutronics",
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    session.add(step)
    await session.flush()
    session.add(
        HpcJobTable(
            job_id="999",
            step_id=step.id,
            user_id=alice.id,
            cluster="odo",
            submitted_at=now_iso(),
        )
    )
    await session.flush()

    await session.delete(run)
    await session.flush()

    steps = (
        await session.exec(
            select(CampaignStepTable).where(CampaignStepTable.run_id == run.id)
        )
    ).all()
    jobs = (
        await session.exec(select(HpcJobTable).where(HpcJobTable.step_id == step.id))
    ).all()
    assert steps == []
    assert jobs == []
