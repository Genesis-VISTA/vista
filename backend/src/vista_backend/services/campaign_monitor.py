"""
Campaign monitor: the background poller that watches in-flight HPC jobs.

Each pass polls every open job (campaign_service.list_open_jobs), records its latest
state, and on a terminal state: collects the result (success) or marks the step failed
(failure), then emails the owning user and stops watching the job. Because it polls the
durable job set globally, a backend restart automatically resumes watching jobs that
outlived the restart — that, plus the persisted message history and job registry, is
what lets a long-running campaign pick up where it left off.

The two external boundaries — polling job status and collecting/parsing a finished job
— are injected, so the reconcile core is unit-testable without MCP or an LLM. The
planner runtime supplies the real closures (commit 8); the app lifespan starts the loop.
"""

import asyncio
import logging
from typing import Awaitable, Callable

from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.forum.simulation import DEBATE_DOMAIN
from ..db.schemas import CampaignRunTable, HpcJobTable, UserTable
from ..services import campaign as campaign_service
from ..services import email as email_service
from ..utils.misc import now_iso


logger = logging.getLogger("vista.campaign_monitor")


# Normalized (upper-case) IRI/SLURM terminal states.
_TERMINAL_SUCCESS = {"COMPLETED", "COMPLETE"}
_TERMINAL_FAILURE = {
    "FAILED",
    "CANCELLED",
    "CANCELED",
    "TIMEOUT",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "BOOT_FAIL",
    "DEADLINE",
    "PREEMPTED",
    "ERROR",
}


def normalize_state(state: str | None) -> str:
    return (state or "").strip().upper()


def is_terminal(state: str | None) -> bool:
    s = normalize_state(state)
    return s in _TERMINAL_SUCCESS or s in _TERMINAL_FAILURE


def is_success(state: str | None) -> bool:
    return normalize_state(state) in _TERMINAL_SUCCESS


def is_failure(state: str | None) -> bool:
    return normalize_state(state) in _TERMINAL_FAILURE


def format_job_notification(
    *, run: CampaignRunTable, job: HpcJobTable, state: str, ok: bool
) -> tuple[str, str]:
    label = run.title or run.domain
    norm = normalize_state(state)
    status = "completed successfully" if ok else f"ended without success ({norm})"
    subject = f"[VISTA] {label}: {job.job_name or 'job'} {norm}"
    body = "\n".join(
        [
            f"Your {run.domain} campaign job has {status}.",
            "",
            f"  campaign: {label}",
            f"  job id:   {job.job_id}",
            f"  role:     {job.job_name or '(unknown)'}",
            f"  cluster:  {job.cluster}",
            f"  state:    {norm}",
            "",
            "Open VISTA to review the results and continue the campaign.",
        ]
    )
    return subject, body


# poll(session, job) -> (state, raw_status); collect(session, job, raw_status, ok) -> None
PollFn = Callable[[AsyncSession, HpcJobTable], Awaitable[tuple[str, str]]]
CollectFn = Callable[[AsyncSession, HpcJobTable, str, bool], Awaitable[None]]
SendEmailFn = Callable[..., Awaitable[bool]]


class CampaignMonitor:
    def __init__(
        self, *, poll: PollFn, collect: CollectFn, send_email: SendEmailFn | None = None
    ):
        self._poll = poll
        self._collect = collect
        self._send_email = send_email or email_service.send_email

    async def reconcile_once(self, session: AsyncSession) -> int:
        """Poll and advance every open job once. Returns the number of jobs examined."""
        jobs = await campaign_service.list_open_jobs(session)
        for job in jobs:
            try:
                await self._process(session, job)
            except Exception:  # noqa: BLE001 - one bad job must not stall the rest
                logger.exception("monitor failed processing job %s", job.job_id)
        return len(jobs)

    async def _process(self, session: AsyncSession, job: HpcJobTable) -> None:
        # Abandon jobs whose campaign was orphaned by multi-session edits — e.g. the
        # conversation backing it was deleted, which sets CampaignRun.session_id NULL (FK
        # SET NULL) so the planner/sandbox can no longer be reconstructed. Stop watching it
        # instead of polling (and failing to resolve) forever.
        if await self._is_orphaned(session, job):
            logger.warning(
                "Abandoning job %s: its campaign is detached from a chat session.",
                job.job_id,
            )
            await campaign_service.update_step(
                session,
                step_id=job.step_id,
                status="failed",
                result={"abandoned": "campaign detached from its chat session"},
            )
            await campaign_service.update_job(
                session, job_id=job.job_id, result_collected=True
            )
            return

        was = job.state  # read before the update below overwrites it
        state, raw_status = await self._poll(session, job)
        await campaign_service.update_job(
            session, job_id=job.job_id, state=state, last_polled_at=now_iso()
        )
        if not is_terminal(state):
            return

        ok = is_success(state)
        if not ok and not is_failure(was):
            # A failure is only believed the second time it is said.
            #
            # A terminal state used to be final on one reading, and the reading
            # comes from a scheduler service that can answer for a job it has not
            # registered yet. On odo that produced FAILED fifty-five seconds after
            # submission for a job whose own log showed it was still building its
            # virtualenv — and because a terminal state also stops the watch, no
            # later poll ever corrected it. The debate was told its simulation had
            # failed, and spent one of its two runs on a job that was alive.
            #
            # Success needs no such confirmation: a scheduler does not report
            # COMPLETED for a job it has not seen.
            logger.info(
                "job %s reported %s from %s; waiting for a second reading before "
                "treating it as failed",
                job.job_id,
                normalize_state(state),
                normalize_state(was) or "(no prior state)",
            )
            return

        # Both outcomes go to the collector. A failed run's log is the most useful
        # thing it produces — it separates "the physics says no" from "the script
        # had a typo" — and this branch used to throw `raw_status` away and record
        # the bare state, so whoever was waiting learned that something failed and
        # never why.
        await self._collect(session, job, raw_status, ok)
        # The monitor owns "stop watching this job" regardless of the collector.
        await campaign_service.update_job(
            session, job_id=job.job_id, result_collected=True
        )
        await self._notify(session, job, state=state, ok=ok)

    async def _is_orphaned(self, session: AsyncSession, job: HpcJobTable) -> bool:
        """
        A job is orphaned when nothing can act on its result any more.

        For a campaign that means losing its chat session: `CampaignRun.session_id`
        goes NULL when the conversation is deleted (FK SET NULL), and the planner
        and sandbox cannot be reconstructed without it, so polling forever would
        achieve nothing.

        A debate-commissioned job is the exception, and not a special case bolted
        on: it never had a chat session, because its result goes back to a forum
        thread rather than to a conversation. Applying the campaign rule to it
        would abandon every such job on its first poll.
        """
        step = await campaign_service.get_step(session, job.step_id)
        if step is None:
            return True
        run = await campaign_service.get_campaign(session, step.run_id)
        if run is None:
            return True
        if run.domain == DEBATE_DOMAIN:
            return False
        return run.session_id is None

    async def _notify(
        self, session: AsyncSession, job: HpcJobTable, *, state: str, ok: bool
    ) -> None:
        if job.notified:
            return
        step = await campaign_service.get_step(session, job.step_id)
        run = (
            await campaign_service.get_campaign(session, step.run_id) if step else None
        )
        user = await session.get(UserTable, job.user_id)
        if run is None or user is None:
            return
        subject, body = format_job_notification(run=run, job=job, state=state, ok=ok)
        await self._send_email(to=user.email, subject=subject, body=body)
        await campaign_service.update_job(session, job_id=job.job_id, notified=True)

    async def run_forever(
        self,
        *,
        session_factory: Callable[[], AsyncSession],
        interval: float = 300.0,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Reconcile loop. `session_factory()` yields an AsyncSession context manager per tick."""
        while not (stop_event is not None and stop_event.is_set()):
            try:
                async with session_factory() as session:
                    examined = await self.reconcile_once(session)
                    await session.commit()
                    if examined:
                        logger.info(
                            "campaign monitor reconciled %d open job(s)", examined
                        )
            except Exception:  # noqa: BLE001 - keep the loop alive across transient failures
                logger.exception("campaign monitor tick failed")
            try:
                if stop_event is not None:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval)
                else:
                    await asyncio.sleep(interval)
            except asyncio.TimeoutError:
                pass


async def resume_open_campaigns(session: AsyncSession) -> list[CampaignRunTable]:
    """The non-terminal campaigns whose open jobs the monitor will resume after a restart."""
    return await campaign_service.list_resumable_campaigns(session)
