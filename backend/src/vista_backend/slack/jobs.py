"""
Tell the researcher in Slack when a job they submitted ends.

`JobMonitor` polls every recorded job (`hpc_job_record`) that has not been
notified: those submitted from Slack, and those from the desktop app when the
desktop-jobs setting is on. It asks `get_hpc_job_status` with the researcher's
own VISTA metadata, as the campaign monitor does, so no credential comes near
Slack. On a terminal state it @mentions the requester, by direct message or
in the thread the job came from, and sets `notified_at` so it is told once.
It reads the database each pass, so a job that ended while VISTA was off is
found on the first pass after it starts.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.campaign.mcp_invoke import build_mcp_invoke, project_paths_for
from ..agents.campaign.wiring import parse_job_state
from ..db.schemas import (
    ChatSessionTable,
    HpcJobRecordTable,
    SlackLinkTable,
    UserPublicWithConfig,
    UserTable,
)
from ..services import chat_run as chat_run_module
from ..services.campaign_monitor import is_success, is_terminal, normalize_state
from . import store
from .access import links_for_user
from .transport import SlackTransport

log = logging.getLogger(__name__)

INTERVAL = 120.0

PollFn = Callable[[AsyncSession, HpcJobRecordTable], Awaitable[str]]
"""(session, record) → the job's state as `get_hpc_job_status` reports it."""


async def poll_status(session: AsyncSession, record: HpcJobRecordTable) -> str:
    """The live poll: `get_hpc_job_status` as the researcher who submitted it."""
    user_row = await session.get(UserTable, record.user_id)
    if user_row is None:
        raise ValueError(f"no user for job {record.job_id}")
    chat = (
        await session.get(ChatSessionTable, record.chat_session_id)
        if record.chat_session_id
        else None
    )
    if chat is None:
        raise ValueError(f"job {record.job_id} has lost its conversation")
    user = UserPublicWithConfig.model_validate(user_row)
    invoke = build_mcp_invoke(user, project_paths_for(chat.project_id, user.id))
    text = await invoke(
        "get_hpc_job_status", {"job_id": record.job_id, "cluster": record.cluster}
    )
    return parse_job_state(text)


def elapsed(start: str, end: str) -> str:
    """`1h 12m`, `4m`, from two ISO times."""
    try:
        seconds = (
            datetime.fromisoformat(end) - datetime.fromisoformat(start)
        ).total_seconds()
    except ValueError:
        return ""
    mins = max(0, int(seconds // 60))
    hours, mins = divmod(mins, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {mins}m" if hours else f"{mins}m"


def message(record: HpcJobRecordTable, slack_user_id: str) -> str:
    state = normalize_state(record.state)
    mark = ":white_check_mark:" if is_success(record.state) else ":x:"
    took = elapsed(record.submitted_at, record.finished_at or record.submitted_at)
    after = f" after {took}" if took else ""
    return (
        f"{mark} <@{slack_user_id}> Your job `{record.job_id}` ({record.job}) on "
        f"{record.cluster} ended: *{state}*{after}."
    )


class JobMonitor:
    def __init__(
        self,
        slack: SlackTransport,
        *,
        poll: PollFn = poll_status,
        session_factory: Callable[[], AsyncSession] | None = None,
    ) -> None:
        self._slack = slack
        self._poll = poll
        self._session_factory = session_factory or (
            lambda: chat_run_module.session_factory()
        )

    async def reconcile_once(self) -> int:
        """Poll and notify every waiting job once. Returns how many were told."""
        async with self._session_factory() as db:
            config = await store.get_config(db)
            desktop = config.notify_desktop_jobs
            await db.commit()
            query = select(HpcJobRecordTable).where(
                col(HpcJobRecordTable.notified_at).is_(None)
            )
            if not desktop:
                query = query.where(HpcJobRecordTable.origin == "slack")
            ids = [r.id for r in (await db.exec(query)).all()]
        told = 0
        for record_id in ids:
            try:
                told += await self._process(record_id)
            except Exception:  # One job must not stall the rest.
                log.exception("Job monitor failed on record %s", record_id)
        return told

    async def _process(self, record_id: uuid.UUID) -> int:
        async with self._session_factory() as db:
            record = await db.get(HpcJobRecordTable, record_id)
            if record is None or record.notified_at is not None:
                return 0
            if not is_terminal(record.state):
                try:
                    state = await self._poll(db, record)
                except Exception as exc:
                    # Lux without a live desktop login lands here; Slack never
                    # asks for one, so the job waits for the next pass.
                    log.info(
                        "Could not poll job %s on %s: %s",
                        record.job_id,
                        record.cluster,
                        type(exc).__name__,
                    )
                    return 0
                record.state = state
                if is_terminal(state):
                    record.finished_at = store.now_iso()
                db.add(record)
                await db.commit()
                await db.refresh(record)
                if not is_terminal(state):
                    return 0
            sent = await self._notify(db, record)
            record.notified_at = store.now_iso()
            db.add(record)
            await db.commit()
            return int(sent)

    async def _link(
        self, db: AsyncSession, record: HpcJobRecordTable
    ) -> SlackLinkTable | None:
        links = await links_for_user(db, record.user_id)
        if record.slack_team_id and record.slack_user_id:
            for link in links:
                if (link.team_id, link.slack_user_id) == (
                    record.slack_team_id,
                    record.slack_user_id,
                ):
                    return link
        return links[0] if links else None

    async def _notify(self, db: AsyncSession, record: HpcJobRecordTable) -> bool:
        """Post the notice; False when there is no one linked to tell."""
        link = await self._link(db, record)
        if link is None:
            return False
        text = message(record, link.slack_user_id)
        in_thread = (
            link.notify_in_thread
            and record.origin == "slack"
            and record.slack_channel_id
            and record.slack_thread_ts
        )
        if in_thread:
            await self._slack.post(
                record.slack_channel_id or "",
                text,
                thread_ts=record.slack_thread_ts,
            )
        else:
            channel = link.dm_channel_id or await self._slack.open_dm(
                link.slack_user_id
            )
            await self._slack.post(channel, text)
        return True

    async def run_forever(
        self, *, interval: float = INTERVAL, stop_event: asyncio.Event
    ) -> None:
        while not stop_event.is_set():
            try:
                await self.reconcile_once()
            except Exception:
                log.exception("Job monitor pass failed")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except TimeoutError:
                pass
