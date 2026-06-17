"""
Campaign service: the durable-state lifecycle for a planner + subagents run.

Pure data layer over the campaign tables (see db/schemas.py) — no HTTP and no
agent logic. The planner runtime (commit 6), the monitor (commit 7), and the API
(commit 8) build on these. Not-found conditions raise ValueError so the service is
usable from non-HTTP contexts (e.g. the background monitor); the API maps these to
404s. Mutators touch `updated_at` and flush + refresh, mirroring chat_session.py.
"""
import uuid

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import (
    CampaignRunTable,
    CampaignStatus,
    CampaignStepStatus,
    CampaignStepTable,
    CampaignUpdate,
    HpcJobTable,
)
from ..utils.misc import now_iso


# Statuses a campaign can still be resumed from after a restart; "converged" and
# "exited" are terminal.
RESUMABLE_STATUSES: tuple[CampaignStatus, ...] = (
    "gathering",
    "planning",
    "running",
    "awaiting_user",
)


# Sentinel so update_* can distinguish "leave unchanged" from "set to None".
_UNSET = object()


# --------------------------------------------------------------------------- #
# CampaignRun
# --------------------------------------------------------------------------- #

async def create_campaign(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    domain: str,
    planner_skill: str,
    session_id: uuid.UUID | None = None,
    title: str | None = None,
    spec: dict | None = None,
) -> CampaignRunTable:
    """Create a new campaign run in the initial `gathering` status."""
    now = now_iso()
    run = CampaignRunTable(
        project_id=project_id,
        user_id=user_id,
        session_id=session_id,
        domain=domain,
        planner_skill=planner_skill,
        title=title,
        spec=spec or {},
        plan=[],
        status="gathering",
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


async def get_campaign(session: AsyncSession, run_id: uuid.UUID) -> CampaignRunTable | None:
    return (
        await session.exec(select(CampaignRunTable).where(CampaignRunTable.id == run_id))
    ).first()


async def require_campaign(session: AsyncSession, run_id: uuid.UUID) -> CampaignRunTable:
    run = await get_campaign(session, run_id)
    if run is None:
        raise ValueError(f"Campaign {run_id} not found")
    return run


async def require_campaign_in_project(
    session: AsyncSession, *, run_id: uuid.UUID, project_id: uuid.UUID
) -> CampaignRunTable:
    """Load a run and confirm it belongs to `project_id` (the API's access boundary)."""
    run = await get_campaign(session, run_id)
    if run is None or run.project_id != project_id:
        raise ValueError(f"Campaign {run_id} not found in project {project_id}")
    return run


async def patch_campaign(
    session: AsyncSession, *, run_id: uuid.UUID, updates: CampaignUpdate
) -> CampaignRunTable:
    """Apply a `CampaignUpdate` (only its set fields) to a run."""
    fields = {}
    if updates.title is not None:
        fields["title"] = updates.title
    if updates.spec is not None:
        fields["spec"] = updates.spec
    if updates.plan is not None:
        fields["plan"] = updates.plan
    if updates.status is not None:
        fields["status"] = updates.status
    return await update_campaign(session, run_id=run_id, **fields)


async def list_campaigns(
    session: AsyncSession,
    *,
    project_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    statuses: tuple[CampaignStatus, ...] | None = None,
) -> list[CampaignRunTable]:
    stmt = select(CampaignRunTable)
    if project_id is not None:
        stmt = stmt.where(CampaignRunTable.project_id == project_id)
    if user_id is not None:
        stmt = stmt.where(CampaignRunTable.user_id == user_id)
    if statuses is not None:
        stmt = stmt.where(col(CampaignRunTable.status).in_(statuses))
    return list((await session.exec(stmt)).all())


async def list_resumable_campaigns(session: AsyncSession) -> list[CampaignRunTable]:
    """Non-terminal campaigns the startup rehydration / monitor should pick up."""
    return await list_campaigns(session, statuses=RESUMABLE_STATUSES)


async def update_campaign(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    title=_UNSET,
    spec=_UNSET,
    plan=_UNSET,
    status=_UNSET,
    session_id=_UNSET,
) -> CampaignRunTable:
    """Patch the provided fields on a run. Omitted fields are left unchanged."""
    run = await require_campaign(session, run_id)
    if title is not _UNSET:
        run.title = title
    if spec is not _UNSET:
        run.spec = spec
    if plan is not _UNSET:
        run.plan = plan
    if status is not _UNSET:
        run.status = status
    if session_id is not _UNSET:
        run.session_id = session_id
    run.updated_at = now_iso()
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


async def set_status(
    session: AsyncSession, *, run_id: uuid.UUID, status: CampaignStatus
) -> CampaignRunTable:
    """Transition a run to `status`. Transition validity is the planner's responsibility."""
    return await update_campaign(session, run_id=run_id, status=status)


async def save_plan(
    session: AsyncSession, *, run_id: uuid.UUID, plan: list[dict]
) -> CampaignRunTable:
    """Persist the editable, user-facing plan (the source of truth the user can amend)."""
    return await update_campaign(session, run_id=run_id, plan=plan)


# --------------------------------------------------------------------------- #
# CampaignStep
# --------------------------------------------------------------------------- #

async def add_step(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    cycle: int,
    kind: str,
    candidate: dict | None = None,
    order_spec: dict | None = None,
) -> CampaignStepTable:
    """Create a `pending` step (a subagent order, or a planner `decision`)."""
    now = now_iso()
    step = CampaignStepTable(
        run_id=run_id,
        cycle=cycle,
        kind=kind,
        candidate=candidate,
        order_spec=order_spec or {},
        status="pending",
        created_at=now,
        updated_at=now,
    )
    session.add(step)
    await session.flush()
    await session.refresh(step)
    return step


async def get_step(session: AsyncSession, step_id: uuid.UUID) -> CampaignStepTable | None:
    return (
        await session.exec(select(CampaignStepTable).where(CampaignStepTable.id == step_id))
    ).first()


async def list_steps(
    session: AsyncSession, *, run_id: uuid.UUID, cycle: int | None = None
) -> list[CampaignStepTable]:
    stmt = select(CampaignStepTable).where(CampaignStepTable.run_id == run_id)
    if cycle is not None:
        stmt = stmt.where(CampaignStepTable.cycle == cycle)
    stmt = stmt.order_by(CampaignStepTable.cycle, CampaignStepTable.created_at)
    return list((await session.exec(stmt)).all())


async def update_step(
    session: AsyncSession,
    *,
    step_id: uuid.UUID,
    status=_UNSET,
    result=_UNSET,
    order_spec=_UNSET,
) -> CampaignStepTable:
    step = await get_step(session, step_id)
    if step is None:
        raise ValueError(f"Campaign step {step_id} not found")
    if status is not _UNSET:
        step.status = status
    if result is not _UNSET:
        step.result = result
    if order_spec is not _UNSET:
        step.order_spec = order_spec
    step.updated_at = now_iso()
    session.add(step)
    await session.flush()
    await session.refresh(step)
    return step


async def set_step_status(
    session: AsyncSession, *, step_id: uuid.UUID, status: CampaignStepStatus
) -> CampaignStepTable:
    return await update_step(session, step_id=step_id, status=status)


# --------------------------------------------------------------------------- #
# HpcJob (the backend's record; complements the MCP server's own registry)
# --------------------------------------------------------------------------- #

async def record_job(
    session: AsyncSession,
    *,
    job_id: str,
    step_id: uuid.UUID,
    user_id: uuid.UUID,
    cluster: str,
    job_name: str | None = None,
    log_path: str | None = None,
    output_dir: str | None = None,
    state: str = "submitted",
) -> HpcJobTable:
    """Record a submitted HPC job against the step that launched it."""
    job = HpcJobTable(
        job_id=job_id,
        step_id=step_id,
        user_id=user_id,
        cluster=cluster,
        job_name=job_name,
        log_path=log_path,
        output_dir=output_dir,
        state=state,
        submitted_at=now_iso(),
    )
    session.add(job)
    await session.flush()
    await session.refresh(job)
    return job


async def get_job(session: AsyncSession, job_id: str) -> HpcJobTable | None:
    return (
        await session.exec(select(HpcJobTable).where(HpcJobTable.job_id == job_id))
    ).first()


async def list_jobs_for_step(
    session: AsyncSession, *, step_id: uuid.UUID
) -> list[HpcJobTable]:
    return list(
        (
            await session.exec(select(HpcJobTable).where(HpcJobTable.step_id == step_id))
        ).all()
    )


async def list_jobs_for_run(
    session: AsyncSession, *, run_id: uuid.UUID
) -> list[HpcJobTable]:
    """All jobs across a run's steps (for the campaign-state / resume view)."""
    stmt = (
        select(HpcJobTable)
        .join(CampaignStepTable, HpcJobTable.step_id == CampaignStepTable.id)
        .where(CampaignStepTable.run_id == run_id)
    )
    return list((await session.exec(stmt)).all())


async def list_open_jobs(session: AsyncSession) -> list[HpcJobTable]:
    """Jobs the monitor should still poll: outputs not yet collected into the step result."""
    return list(
        (
            await session.exec(
                select(HpcJobTable).where(col(HpcJobTable.result_collected).is_(False))
            )
        ).all()
    )


async def update_job(
    session: AsyncSession,
    *,
    job_id: str,
    state=_UNSET,
    last_polled_at=_UNSET,
    notified=_UNSET,
    result_collected=_UNSET,
) -> HpcJobTable:
    job = await get_job(session, job_id)
    if job is None:
        raise ValueError(f"HPC job {job_id} not found")
    if state is not _UNSET:
        job.state = state
    if last_polled_at is not _UNSET:
        job.last_polled_at = last_polled_at
    if notified is not _UNSET:
        job.notified = notified
    if result_collected is not _UNSET:
        job.result_collected = result_collected
    session.add(job)
    await session.flush()
    await session.refresh(job)
    return job
