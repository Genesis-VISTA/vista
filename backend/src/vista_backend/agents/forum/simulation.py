"""
Simulation in the loop: a debate commissioning HPC work to test a prediction.

The Proposer and Reviewer produce falsifiable predictions; this is how one gets
tested instead of argued about. A role commissions a job, the existing campaign
monitor watches it, and when it finishes the result is posted back **as the
commissioning role** — so the evidence carries the same identity as the claim it
bears on.

Two facts about the shapes involved drive the design:

  - **An HPC job outlives a round, and the role waits for it anyway.** This was
    once the other way round: a commission returned immediately and the result
    landed on the thread whenever it landed. That was true to the shapes involved
    and wrong in practice — the answer usually arrived after the verdict, where
    no agent ever reasoned about it, so the debate paid for a simulation and then
    argued without it. Now the role that asked the question waits for the answer,
    bounded by `forum.max_job_wait_seconds`; on timeout the debate carries on and the
    result still reaches the thread.

    Waiting has one hard requirement: nothing may hold a database transaction
    across it. A waiting role is waiting for the *monitor* to record a result, and
    on SQLite an open write transaction stops the monitor writing — the debate
    would block what it is waiting for. Hence the commissioner's own session, the
    per-poll sessions in `wait_for_result`, and the orchestrator committing before
    a role speaks.
  - **The identity outlives the debate.** A debate keeps one roster for its whole
    life, so a result that lands after the verdict still posts under the role that
    asked for it.

The job itself is tracked as a one-step campaign, because "submit a job, watch
it, collect it" is exactly what the campaign tables and monitor already do.
Building a second poller beside that one would be a second thing to get wrong.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from ...db.schemas import CampaignRunTable, HpcJobTable
from ...services import campaign as campaign_service
from ...services.forum_git import ForumClient, Participant, PostKind, ThreadClosed
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


class CommissionRefused(ValueError):
    """
    A commission turned down before any job existed.

    Subclassed rather than raised directly, because the two kinds ask opposite
    things of the role that tried. A `BadCommission` is the role's own mistake and
    a corrected call works; a `BudgetSpent` is final for the debate. Everything
    else that comes out of a submission — an S3M token minted for the wrong
    project, a scratch directory the submitter could not create — is neither: only
    a human can clear it, and the role should stop asking.
    """


class BadCommission(CommissionRefused):
    """The job or cluster named is not one this debate has. Try again, corrected."""


class BudgetSpent(CommissionRefused):
    """This debate has used its simulation allowance. No later call can work."""


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


def clusters_for_job(job: str, catalog: Path) -> list[str]:
    """
    The clusters a job's own `cluster_defaults.json` defines.

    A job is not portable: `salt-neutronics-tbr` has sections for odo and
    perlmutter, `salt-chemistry-md` only for frontier. Submitting to a cluster the
    job has no section for is refused by `submit_hpc_job`, so the set has to be
    read here rather than discovered by being told no.
    """
    path = catalog / job / "cluster_defaults.json"
    try:
        defaults = json.loads(path.read_text())
    except OSError, json.JSONDecodeError:
        return []
    return sorted(defaults) if isinstance(defaults, dict) else []


def runnable_simulations(
    project_skills: list[str], catalog: Path, user_clusters: list[str]
) -> dict[str, list[str]]:
    """
    Each job this debate may run, and where it can actually run it.

    The intersection of three things, and all three matter: the project's loaded
    skills, the jobs on disk, and the clusters the opener has credentials for.

    Kept as a mapping rather than two lists because two lists cannot express the
    thing that broke. With `["salt-neutronics-tbr"]` and
    `["frontier", "odo", "perlmutter"]` the only available default was
    `clusters[0]` — alphabetical, unrelated to the job — which picked frontier, the
    one cluster that job has no section for. Nothing was launched, and the agent
    was shown a cluster list it could not choose correctly from.
    """
    allowed = set(user_clusters)
    pairs = (
        (job, [c for c in clusters_for_job(job, catalog) if c in allowed])
        for job in runnable_jobs(project_skills, catalog)
    )
    return {job: clusters for job, clusters in pairs if clusters}


_FLAG = re.compile(r"--[a-z][a-z0-9-]{2,}")
"""A long option in a job's README — the marker for "this line is about arguments"."""

USAGE_CHARS = 900
"""
Ceiling on one job's usage note.

Big enough for the whole argument list of both jobs on this deployment (466 and
637 characters), small enough that offering it every turn costs less than one
wasted submission.
"""


def usage_for_job(job: str, catalog: Path) -> str:
    """
    How to invoke a job, taken from the job's own README.

    `script_args` is a free-text string, and a role given no help with it invents
    plausible flags: `--salt flibe_90Li6 --geometry arc_lib --multiplier
    beberyllide_nearwall_30cm`, against a script that accepts `--bef2`, `--li6`,
    `--be-multiplier`, `--nominal-bef2` and `--allow-extrapolation`. argparse
    exits 2 on an unknown option, so the job burns a submission and a slot in the
    debate's budget to print a usage message onto a stream nobody reads.

    The README already answers this — `hpc_jobs/<job>/README.md` documents the
    flags and gives a worked `script_args=` example. It was simply never shown to
    a debating role: `read_domain_guidance` serves the *skill* body, and for this
    skill that documents a different interface (`python -m salt_neutronics.cli`)
    from the one the HPC wrapper exposes.

    Extraction is deliberately "every line mentioning a long option" rather than a
    section parse. A heading convention is a thing for the next job's README to
    get subtly wrong; a line with `--flag` in it is about arguments in any layout,
    and the few neighbouring lines it also catches — a range caveat, a "you MUST
    pass" warning — are worth having.
    """
    readme = catalog / job / "README.md"
    try:
        text = readme.read_text()
    except OSError:
        return ""
    lines = [line.rstrip() for line in text.splitlines() if _FLAG.search(line)]
    if not lines:
        return ""
    usage = "\n".join(lines)
    if len(usage) > USAGE_CHARS:
        usage = usage[:USAGE_CHARS] + "\n… see the job's README for the rest."
    return usage


def usage_for(jobs: Iterable[str], catalog: Path) -> dict[str, str]:
    """Usage notes for the jobs that have one, keyed by job."""
    found = ((job, usage_for_job(job, catalog)) for job in jobs)
    return {job: usage for job, usage in found if usage}


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
        wait: bool = True,
    ) -> JobOutcome: ...


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


class JobOutcome(BaseModel):
    """
    What a role learned by waiting for the job it commissioned.

    `timed_out` is a distinct outcome from failure. The job is still running and
    its result will still reach the thread; what ran out is the debate's patience,
    and a role told "it failed" when it merely has not finished yet would reason
    from a false negative.
    """

    job_id: str
    finished: bool = False
    ok: bool | None = None
    """None while unknown — nobody has seen a terminal state yet."""

    state: str = ""
    outputs: str = ""
    timed_out: bool = False

    uncollectable: bool = False
    """
    The job was submitted and nothing will ever collect it.

    True when the campaign monitor is disabled, which is its default. Distinct
    from `timed_out`: that job is still being watched and its result will arrive,
    this one will sit at `submitted` forever. Saying "the result will be posted
    when it finishes" here would be a promise the deployment cannot keep, written
    into the permanent record of the debate.
    """


async def wait_for_result(
    session_factory: Callable[[], AsyncSession],
    *,
    job_id: str,
    timeout: float,
    poll_seconds: float,
) -> JobOutcome:
    """
    Block until a commissioned job finishes, or until the debate gives up.

    Polls the database rather than the cluster, deliberately. The campaign monitor
    is the only thing that polls a scheduler, and a second poller would race it on
    the same rows — two `_process` passes could both decide a job needs collecting.
    So this watches for the monitor's own conclusion, which means the floor on
    noticing is `campaigns.monitor_interval` and not `poll_seconds`.

    Each poll opens its own session and closes it. A long-lived session here would
    hold a read transaction across the whole wait, and on SQLite that is enough to
    keep the monitor from writing the very update being waited for.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        async with session_factory() as session:
            job = await campaign_service.get_job(session, job_id)
            if job is None:
                return JobOutcome(job_id=job_id, state="unknown")
            step = await campaign_service.get_step(session, job.step_id)
            done = bool(job.result_collected) or (
                step is not None and step.status in ("completed", "failed")
            )
            if done:
                result = (step.result if step is not None else None) or {}
                state = str(result.get("state") or job.state)
                return JobOutcome(
                    job_id=job_id,
                    finished=True,
                    ok=(step.status == "completed") if step is not None else None,
                    state=state,
                    outputs=str(result.get("outputs") or ""),
                )
            state_now = job.state

        if asyncio.get_running_loop().time() >= deadline:
            logger.info(
                "debate: gave up waiting on job %s after %.0fs (state %s)",
                job_id,
                timeout,
                state_now,
            )
            return JobOutcome(job_id=job_id, state=state_now, timed_out=True)
        await asyncio.sleep(poll_seconds)


class CommissionedRun(BaseModel):
    """
    One simulation a debate commissioned, and where it got to.

    Exists because the debate record alone cannot answer the question a reader
    actually has. A `commission_simulation` entry proves a job was *submitted*;
    whether it ran, failed, or is still queued lives on the campaign side, and
    without joining the two a reader sees a job id and no way to find out what
    became of it.
    """

    job_id: str
    job_name: str | None = None
    """
    Nullable on the job row, so nullable here.

    A debate always names a job when it commissions one, but this reads the HPC
    table rather than the order, and coercing a missing name to `""` would show a
    reader a blank where the honest answer is that the row does not carry it.
    """

    cluster: str
    prediction: str
    """The prediction the run was commissioned to settle."""

    commissioned_by: str
    """The forum identity that asked for it, which is who the result posts as."""

    state: str
    """The scheduler's own word for it — `submitted`, `RUNNING`, `COMPLETED`, …"""

    submitted_at: str = ""
    last_polled_at: str | None = None
    """
    None means nothing has looked at this job since it was submitted.

    Worth surfacing rather than smoothing over: a job that was never polled is
    not a job that is running slowly, and the two are indistinguishable from the
    state alone.
    """

    result_collected: bool = False
    """Whether the outputs came back and were posted onto the thread."""


async def commissioned_runs(
    session: AsyncSession, *, debate_run_id: uuid.UUID
) -> list[CommissionedRun]:
    """
    Every simulation this debate commissioned, finished or not.

    Unlike `open_simulations`, which lists only jobs still out, this includes
    completed and failed runs because "it finished an hour ago and posted nothing" is exactly the state a
    reader needs to see.
    """
    wanted = str(debate_run_id)
    out: list[CommissionedRun] = []
    for run in await campaign_service.list_campaigns(session):
        if run.domain != DEBATE_DOMAIN or run.spec.get("debate_run_id") != wanted:
            continue
        for job in await campaign_service.list_jobs_for_run(session, run_id=run.id):
            out.append(
                CommissionedRun(
                    job_id=job.job_id,
                    job_name=job.job_name,
                    cluster=job.cluster,
                    prediction=run.spec.get("prediction", ""),
                    commissioned_by=run.spec.get("commissioned_by", ""),
                    state=job.state,
                    submitted_at=job.submitted_at,
                    last_polled_at=job.last_polled_at,
                    result_collected=bool(job.result_collected),
                )
            )
    return sorted(out, key=lambda r: r.submitted_at or "")


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
    identity as the claim it bears on — a result attributed to the operator would
    read as the operator vouching for it.

    A closed thread is not an error: the human ended the debate while the job was
    running, which is allowed and common. The result is recorded on the step
    either way, so nothing is lost even when the thread will not take it.
    """
    run = await _campaign_for_job(session, job)
    if run is None:
        return False

    spec = run.spec
    participant = participant_from_identity(spec["commissioned_by"])
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


def participant_from_identity(identity: str) -> Participant:
    """
    The role behind an identity like `vista-reviewer-1a2b3c4d`.

    The campaign spec keeps only the identity, which names its role; a spec from
    before that convention falls back to the identity itself as the role name.
    """
    parts = identity.split("-")
    role = parts[1] if len(parts) >= 3 and parts[0] == "vista" else identity
    return Participant(identity=identity, role=role)


def spec_of(run: CampaignRunTable) -> dict[str, Any]:
    """The debate context carried on a commissioned campaign."""
    return run.spec
