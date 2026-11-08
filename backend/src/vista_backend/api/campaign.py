"""
Campaign API — CRUD + state/resume for multi-agent campaigns.

Campaigns are scoped under a project (`project_service.get_project_by_name` 404s an
unknown one). Routes are thin; the logic lives in `services/campaign.py` (tested at
the service layer).
"""

import uuid

from fastapi import APIRouter, HTTPException

from ..db.db import SessionDep
from ..db.schemas import (
    CampaignCreate,
    CampaignRunPublic,
    CampaignStatePublic,
    CampaignStepPublic,
    CampaignUpdate,
    HpcJobPublic,
)
from ..services import campaign as campaign_service
from ..services import project as project_service


router = APIRouter(prefix="/projects", tags=["campaigns"])


@router.post("/{project_name}/campaigns")
async def create_campaign(
    project_name: str, body: CampaignCreate, session: SessionDep
) -> CampaignRunPublic:
    project = await project_service.get_project_by_name(session, project_name)
    run = await campaign_service.create_campaign(
        session,
        project_id=project.id,
        domain=body.domain,
        planner_skill=body.planner_skill,
        title=body.title,
        spec=body.spec,
    )
    return CampaignRunPublic.model_validate(run)


@router.get("/{project_name}/campaigns")
async def list_campaigns(
    project_name: str,
    session: SessionDep,
    chat_session_id: uuid.UUID | None = None,
) -> list[CampaignRunPublic]:
    project = await project_service.get_project_by_name(session, project_name)
    runs = await campaign_service.list_campaigns(
        session, project_id=project.id, session_id=chat_session_id
    )
    return [CampaignRunPublic.model_validate(r) for r in runs]


@router.get("/{project_name}/campaigns/{run_id}")
async def get_campaign(
    project_name: str, run_id: uuid.UUID, session: SessionDep
) -> CampaignStatePublic:
    project = await project_service.get_project_by_name(session, project_name)
    run = await _require_in_project(session, run_id, project.id)
    steps = await campaign_service.list_steps(session, run_id=run.id)
    jobs = await campaign_service.list_jobs_for_run(session, run_id=run.id)
    return CampaignStatePublic(
        run=CampaignRunPublic.model_validate(run),
        steps=[CampaignStepPublic.model_validate(s) for s in steps],
        jobs=[HpcJobPublic.model_validate(j) for j in jobs],
    )


@router.patch("/{project_name}/campaigns/{run_id}")
async def patch_campaign(
    project_name: str,
    run_id: uuid.UUID,
    body: CampaignUpdate,
    session: SessionDep,
) -> CampaignRunPublic:
    project = await project_service.get_project_by_name(session, project_name)
    await _require_in_project(session, run_id, project.id)
    run = await campaign_service.patch_campaign(session, run_id=run_id, updates=body)
    return CampaignRunPublic.model_validate(run)


async def _require_in_project(session, run_id: uuid.UUID, project_id: uuid.UUID):
    try:
        return await campaign_service.require_campaign_in_project(
            session, run_id=run_id, project_id=project_id
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Campaign not found")
