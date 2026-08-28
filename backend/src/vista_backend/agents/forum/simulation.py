"""
Simulation in the loop: a debate commissioning HPC work to test a prediction.

The Proposer and Reviewer produce falsifiable predictions; this is how one gets
tested instead of argued about. A role commissions a job, the existing campaign
monitor watches it, and when it finishes the result is posted back **through the
commissioning role's box** — so the evidence carries the same host-stamped
identity as the claim it bears on.

Two facts about the shapes involved drive the design:

  - **An HPC job outlives the debate.** Rounds take seconds; a job takes minutes
    to hours. So a commission does not block a round. The result lands on the
    thread whenever it lands — during a later round if the debate is still
    running, or after the verdict if it is not, which is honest either way.
  - **Attribution requires the box to still exist.** A revoked participant cannot
    post, so the orchestrator does not retire a role while it has work in flight.
    `reap_after_collection` retires it once the result is in.

The job itself is tracked as a one-step campaign, because "submit a job, watch
it, collect it" is exactly what the campaign tables and monitor already do.
Building a second poller beside that one would be a second thing to get wrong.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Protocol

from sqlmodel.ext.asyncio.session import AsyncSession

from ...db.schemas import CampaignRunTable, HpcJobTable
from ...services import campaign as campaign_service
from ...services import debate as debate_service
from ...services.h5i_forum import ForumClient, Participant, PostKind, ThreadClosed
from ..campaign.subagent import HpcTools


logger = logging.getLogger(__name__)


DEBATE_DOMAIN = "debate"
"""
`CampaignRun.domain` for a debate-commissioned job.

Also the flag the monitor's orphan rule reads: a debate campaign legitimately has
no chat session, and without this it would be abandoned on its first poll.
"""


# Which cluster each backend credential unlocks. `s3m_token` is the generic OLCF
# token and covers both OLCF machines; NERSC is a separate credential. Mirrors
# `submit_job_mcp.configured_clusters`, and the two must not drift — a debate
# that offers a cluster the user cannot reach wastes a submission.
CLUSTER_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "odo": ("s3m_token",),
    "frontier": ("s3m_token",),
    "perlmutter": ("nersc_iri_token",),
}


def clusters_for(user: Any) -> list[str]:
    """The clusters this user actually has credentials for."""
    return sorted(
        cluster
        for cluster, fields in CLUSTER_CREDENTIALS.items()
        if any(getattr(user, field, None) for field in fields)
    )


def runnable_jobs(project_skills: list[str], catalog: Path) -> list[str]:
    """
    The jobs a debate in this project may submit.

    A debate is scoped to a project, so its simulations are the project's own
    loaded skills — no separate allowlist to keep in sync with what the project
    is actually for. A simulation skill is bound to the job of the same name (the
    convention `campaign.yaml` also encodes as `skill: x, job: x`), so a skill
    with no matching job directory contributes nothing. That is how a planner
    skill like `splash-planner` stays out of this list without special-casing.
    """
    if not catalog.is_dir():
        return []
    available = {p.name for p in catalog.iterdir() if p.is_dir()}
    return sorted(set(project_skills) & available)


async def commissioned_count(session: AsyncSession, *, debate_run_id: uuid.UUID) -> int:
    """
    How many jobs this debate has commissioned, finished or not.

    Counts every one, not just the ones still running: the cap is on how much a
    debate may spend, and a job that already completed still spent it.
    """
    runs = await campaign_service.list_campaigns(session)
    wanted = str(debate_run_id)
    return sum(
        1
        for run in runs
        if run.domain == DEBATE_DOMAIN and run.spec.get("debate_run_id") == wanted
    )


class SimulationCommissioner(Protocol):
    """What the debate tool needs to start a job. Injected so tests need no MCP."""

    async def __call__(
        self,
        *,
        participant: Participant,
        job: str,
        prediction: str,
        cluster: str | None = None,
        script_args: str | None = None,
        reply_to: str | None = None,
    ) -> str: ...


async def commission(
    session: AsyncSession,
    *,
    debate_run_id: uuid.UUID,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    thread_id: str,
    participant: Participant,
    hpc: HpcTools,
    job: str,
    prediction: str,
    cluster: str | None = None,
    script_args: str | None = None,
    reply_to: str | None = None,
) -> HpcJobTable:
    """
    Submit a job to test one prediction, and record who asked and why.

    The `spec` carries everything the collector needs to post the answer back to
    the right thread as the right role. It is deliberately self-contained: the
    collector runs in the monitor, minutes or hours later, in a different session
    and possibly a different process.
    """
    run = await campaign_service.create_campaign(
        session,
        project_id=project_id,
        user_id=user_id,
        domain=DEBATE_DOMAIN,
        planner_skill="",
        title=f"Testing: {prediction[:80]}",
        spec={
            "debate_run_id": str(debate_run_id),
            "thread_id": thread_id,
            "commissioned_by": participant.identity,
            "box_slug": participant.box_slug,
            "box_id": participant.box_id,
            "prediction": prediction,
            "reply_to": reply_to,
        },
    )
    step = await campaign_service.add_step(
        session,
        run_id=run.id,
        cycle=0,
        kind="simulation",
        order_spec={"job": job, "prediction": prediction},
    )

    submitted = await hpc.submit(
        job=job,
        cluster=cluster,
        node_count=None,
        duration=None,
        script_args=script_args,
    )
    row = await campaign_service.record_job(
        session,
        job_id=submitted.job_id,
        step_id=step.id,
        user_id=user_id,
        cluster=submitted.cluster,
        job_name=job,
        log_path=submitted.log_path,
        output_dir=submitted.output_dir,
    )
    await campaign_service.set_step_status(
        session, step_id=step.id, status="dispatched"
    )
    return row


async def open_simulations(
    session: AsyncSession, *, debate_run_id: uuid.UUID
) -> list[HpcJobTable]:
    """
    Jobs this debate is still waiting on.

    Filtered in Python over debate-domain campaigns rather than by a JSON query,
    because `spec` is a JSON column and the set is small — a debate commissions a
    handful of jobs, not thousands.
    """
    open_jobs = await campaign_service.list_open_jobs(session)
    if not open_jobs:
        return []

    wanted = str(debate_run_id)
    matching: list[HpcJobTable] = []
    for job in open_jobs:
        run = await _campaign_for_job(session, job)
        if run is not None and run.spec.get("debate_run_id") == wanted:
            matching.append(job)
    return matching


async def _campaign_for_job(
    session: AsyncSession, job: HpcJobTable
) -> CampaignRunTable | None:
    step = await campaign_service.get_step(session, job.step_id)
    if step is None:
        return None
    run = await campaign_service.get_campaign(session, step.run_id)
    if run is None or run.domain != DEBATE_DOMAIN:
        return None
    return run


# --------------------------------------------------------------------------- #
# Posting the answer back
# --------------------------------------------------------------------------- #


def format_report(
    *, prediction: str, job: HpcJobTable, state: str, outputs: str
) -> str:
    """The attachment: what was run, against which prediction, and what came out."""
    return json.dumps(
        {
            "prediction": prediction,
            "job_id": job.job_id,
            "job_name": job.job_name,
            "cluster": job.cluster,
            "state": state,
            "output_dir": job.output_dir,
            "log_path": job.log_path,
            "outputs": outputs,
        },
        indent=2,
    )


def format_finding(*, prediction: str, job: HpcJobTable, ok: bool, outputs: str) -> str:
    """
    The post body.

    Leads with the result rather than the provenance, because a peer reading the
    thread wants to know what the simulation said; the job id and the full report
    are below it and attached.
    """
    verdict = "ran to completion" if ok else "did not complete"
    head = (
        f"The simulation commissioned against “{prediction}” {verdict}."
        if not ok
        else f"Simulation result for “{prediction}”."
    )
    return (
        f"{head}\n\n"
        f"{outputs.strip() or '(the job produced no parsed output)'}\n\n"
        f"`{job.job_name or 'job'}` · job {job.job_id} on {job.cluster} · "
        "full report attached."
    )


async def post_result(
    session: AsyncSession,
    client: ForumClient,
    job: HpcJobTable,
    *,
    state: str,
    ok: bool,
    outputs: str,
) -> bool:
    """
    Post a finished job's result back onto the debate thread. Returns whether it landed.

    Posted as the role that commissioned it, so the evidence carries the same
    host-stamped identity as the claim it bears on — a result attributed to the
    host would read as the operator vouching for it.

    A closed thread is not an error: the human ended the debate while the job was
    running, which is allowed and common. The result is recorded on the step
    either way, so nothing is lost even when the thread will not take it.
    """
    run = await _campaign_for_job(session, job)
    if run is None:
        return False

    spec = run.spec
    participant = Participant(
        identity=spec["commissioned_by"],
        role="worker",  # type: ignore[arg-type]
        box_slug=spec["box_slug"],
        box_id=spec["box_id"],
    )
    prediction = spec.get("prediction", "")

    await campaign_service.update_step(
        session,
        step_id=job.step_id,
        status="completed" if ok else "failed",
        result={"state": state, "outputs": outputs},
    )

    try:
        attachment = await client.stage_attachment(
            participant,
            f"simulation-{job.job_id}.json",
            format_report(prediction=prediction, job=job, state=state, outputs=outputs),
        )
        await client.post_as(
            participant,
            spec["thread_id"],
            format_finding(prediction=prediction, job=job, ok=ok, outputs=outputs),
            kind=PostKind.FINDING,
            reply_to=spec.get("reply_to"),
            attachment=attachment,
            attachment_kind="test-report",
        )
        return True
    except ThreadClosed:
        logger.info(
            "debate %s: job %s finished after the thread closed; result kept on the step",
            spec.get("debate_run_id"),
            job.job_id,
        )
        return False
    except Exception:  # noqa: BLE001 — a failed post must not strand the job
        logger.exception("debate: could not post job %s back to the thread", job.job_id)
        return False


async def reap_after_collection(
    session: AsyncSession, client: ForumClient, job: HpcJobTable
) -> None:
    """
    Retire the commissioning role once its last job is in.

    The orchestrator leaves a role attached while it has work in flight, because
    a revoked participant cannot post. Something has to take it off the forum
    afterwards, and the collector is the only code that knows the work is done.
    """
    run = await _campaign_for_job(session, job)
    if run is None:
        return
    debate_run_id = uuid.UUID(run.spec["debate_run_id"])

    debate = await debate_service.get_debate(session, debate_run_id)
    if debate is None or debate.status in debate_service.ACTIVE_STATUSES:
        return  # the debate is still arguing; it owns its own roster
    if await open_simulations(session, debate_run_id=debate_run_id):
        return  # other jobs are still out

    for row in await debate_service.list_participants(session, run_id=debate_run_id):
        if not row.active:
            continue
        await client.remove_participant(
            Participant(
                identity=row.identity,
                role=row.forum_role,  # type: ignore[arg-type]
                box_slug=row.box_slug,
                box_id=row.box_id,
            )
        )
        await debate_service.deactivate_participant(
            session, run_id=debate_run_id, identity=row.identity
        )


def spec_of(run: CampaignRunTable) -> dict[str, Any]:
    """The debate context carried on a commissioned campaign."""
    return run.spec
