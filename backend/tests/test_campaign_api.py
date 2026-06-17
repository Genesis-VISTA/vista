"""Service-layer tests for the campaign API's access boundary + state assembly.

The routes are thin wrappers over these (project membership is the access boundary,
enforced by project_service.get_project_by_name); the repo tests this logic at the
service layer.
"""
import uuid

import pytest

from vista_backend.db.schemas import CampaignUpdate, ProjectCreate
from vista_backend.services import campaign as campaign_service
from vista_backend.services import project as project_service


async def _project(session, user, name="api-campaign"):
    return await project_service.create_project(session, ProjectCreate(name=name), user)


@pytest.mark.anyio
async def test_require_campaign_in_project_enforces_project_scope(session, alice):
    project = await _project(session, alice)
    other = await _project(session, alice, name="other-project")
    run = await campaign_service.create_campaign(
        session, project_id=project.id, user_id=alice.id,
        domain="splash", planner_skill="splash-planner",
    )

    found = await campaign_service.require_campaign_in_project(
        session, run_id=run.id, project_id=project.id
    )
    assert found.id == run.id

    # Same run, wrong project -> not found.
    with pytest.raises(ValueError):
        await campaign_service.require_campaign_in_project(
            session, run_id=run.id, project_id=other.id
        )
    # Unknown run -> not found.
    with pytest.raises(ValueError):
        await campaign_service.require_campaign_in_project(
            session, run_id=uuid.uuid4(), project_id=project.id
        )


@pytest.mark.anyio
async def test_list_campaigns_scoped_to_project(session, alice):
    p1 = await _project(session, alice, name="p1")
    p2 = await _project(session, alice, name="p2")
    r1 = await campaign_service.create_campaign(
        session, project_id=p1.id, user_id=alice.id, domain="d", planner_skill="s"
    )
    await campaign_service.create_campaign(
        session, project_id=p2.id, user_id=alice.id, domain="d", planner_skill="s"
    )
    in_p1 = await campaign_service.list_campaigns(session, project_id=p1.id)
    assert [r.id for r in in_p1] == [r1.id]


@pytest.mark.anyio
async def test_patch_campaign_applies_only_set_fields(session, alice):
    project = await _project(session, alice)
    run = await campaign_service.create_campaign(
        session, project_id=project.id, user_id=alice.id,
        domain="splash", planner_skill="splash-planner", title="orig", spec={"a": 1},
    )

    patched = await campaign_service.patch_campaign(
        session, run_id=run.id, updates=CampaignUpdate(status="running", plan=[{"step": 1}]),
    )
    assert patched.status == "running"
    assert patched.plan == [{"step": 1}]
    # Unset fields preserved.
    assert patched.title == "orig"
    assert patched.spec == {"a": 1}


@pytest.mark.anyio
async def test_list_jobs_for_run_spans_steps(session, alice):
    project = await _project(session, alice)
    run = await campaign_service.create_campaign(
        session, project_id=project.id, user_id=alice.id, domain="d", planner_skill="s"
    )
    s1 = await campaign_service.add_step(session, run_id=run.id, cycle=0, kind="neutronics")
    s2 = await campaign_service.add_step(session, run_id=run.id, cycle=0, kind="chemistry")
    await campaign_service.record_job(
        session, job_id="j1", step_id=s1.id, user_id=alice.id, cluster="odo"
    )
    await campaign_service.record_job(
        session, job_id="j2", step_id=s2.id, user_id=alice.id, cluster="odo"
    )

    jobs = await campaign_service.list_jobs_for_run(session, run_id=run.id)
    assert {j.job_id for j in jobs} == {"j1", "j2"}
