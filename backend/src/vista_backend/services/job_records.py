"""
A record of every HPC job a researcher submits through a chat, from any channel.

`SubmitWatcher` sits in a chat run and, when `submit_hpc_job` returns a
summary, writes an `hpc_job_record` row at once: the cluster and job id it
reported, what was asked for, and, for a Slack turn, the thread to report back
to. `list_my_recent_jobs` reads them, so "my last job" means the last job this
researcher submitted, not whatever the model remembers. Campaign jobs keep
their own table and are not recorded here.

A job submitted in a Slack conversation is also watched (`background_tasks`,
as `watch_hpc_job` would), so its end starts a turn the thread is shown; the
watch writes the final state back here.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ToolReturnPart,
)
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.campaign.hpc_tools import parse_submit_summary
from ..db.schemas import ChatSessionTable, HpcJobRecordTable, SlackThreadTable

log = logging.getLogger(__name__)

SUBMIT = "submit_hpc_job"


def _is_submit(tool_name: str) -> bool:
    return tool_name.split("__")[-1] == SUBMIT


def _text(part: ToolReturnPart) -> str:
    content = part.content
    return content if isinstance(content, str) else part.model_response_str()


class SubmitWatcher:
    def __init__(
        self,
        *,
        session_factory: Callable[[], AsyncSession],
        chat_session_id: uuid.UUID,
        channel: str,
    ) -> None:
        self._session_factory = session_factory
        self._chat_session_id = chat_session_id
        self._channel = channel
        self._args: dict[str, dict[str, Any]] = {}

    async def observe(self, event: object) -> None:
        """Every event of the run; only a submit's call and result matter."""
        if isinstance(event, FunctionToolCallEvent) and _is_submit(
            event.part.tool_name
        ):
            self._args[event.part.tool_call_id] = event.part.args_as_dict()
        elif isinstance(event, FunctionToolResultEvent) and isinstance(
            event.part, ToolReturnPart
        ):
            args = self._args.pop(event.part.tool_call_id, None)
            if args is not None:
                await self._record(args, _text(event.part))

    async def _record(self, args: dict[str, Any], summary: str) -> None:
        try:
            found = parse_submit_summary(summary)
        except ValueError:
            return  # Not a summary: the submit failed, or was refused.
        # The Slack (team, user) who asked, so the audit row names their link.
        asker: tuple[str | None, str | None] = (None, None)
        try:
            async with self._session_factory() as db:
                thread = None
                if self._channel == "slack":
                    thread = (
                        await db.exec(
                            select(SlackThreadTable).where(
                                SlackThreadTable.chat_session_id
                                == self._chat_session_id
                            )
                        )
                    ).first()
                if thread is not None:
                    asker = (thread.team_id, thread.slack_user_id)
                db.add(
                    HpcJobRecordTable(
                        chat_session_id=self._chat_session_id,
                        origin=self._channel,
                        slack_team_id=thread.team_id if thread else None,
                        slack_channel_id=thread.channel_id if thread else None,
                        slack_thread_ts=thread.thread_ts if thread else None,
                        slack_user_id=thread.slack_user_id if thread else None,
                        cluster=found["cluster"] or str(args.get("cluster") or ""),
                        job_id=found["job_id"],
                        job=str(args.get("job") or ""),
                        node_count=_int(args.get("node_count")),
                        duration=_str(args.get("duration")),
                        script_args=_str(args.get("script_args")),
                        submitted_at=datetime.now(UTC).isoformat(timespec="seconds"),
                    )
                )
                await db.commit()
        except IntegrityError:
            log.info("Job %s is already recorded", found["job_id"])
            return
        except Exception:
            log.exception("Could not record job %s", found["job_id"])
        if self._channel == "slack":
            await self._watch(
                found["cluster"] or str(args.get("cluster") or ""), found["job_id"]
            )
            from ..slack import audit  # Here, as it imports the chat run.

            await audit.record(
                "tool_result",
                team_id=asker[0],
                slack_user_id=asker[1],
                chat_session_id=self._chat_session_id,
                tool_name=SUBMIT,
                detail={"args": args, "cluster": found["cluster"]},
                job_id=found["job_id"],
            )

    async def _watch(self, cluster: str, job_id: str) -> None:
        """Watch a job submitted from Slack, so its end is reported in the thread."""
        from . import background_tasks  # Here, as it imports the chat run.

        try:
            async with self._session_factory() as db:
                chat = await db.get(ChatSessionTable, self._chat_session_id)
                project_id = chat.project_id if chat is not None else None
                await db.commit()
            if project_id is None:
                return
            async with background_tasks.session_factory() as db:
                record = await background_tasks.create_watch(
                    db,
                    chat_session_id=self._chat_session_id,
                    project_id=project_id,
                    job_id=job_id,
                    cluster=cluster,
                )
        except background_tasks.TaskRefused as refused:
            log.info("Job %s is not watched: %s", job_id, refused)
            return
        except Exception:
            log.exception("Could not watch job %s", job_id)
            return
        background_tasks.background_task_registry.watch_job(
            record, read=background_tasks.shared_state_reader()
        )


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


async def set_final_state(
    db: AsyncSession, *, cluster: str, job_id: str, state: str
) -> None:
    """Record how a job ended, on its record if it has one."""
    rows = (
        await db.exec(
            select(HpcJobRecordTable).where(
                HpcJobRecordTable.cluster == cluster,
                HpcJobRecordTable.job_id == job_id,
            )
        )
    ).all()
    for row in rows:
        row.state = state
        row.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
        db.add(row)
    await db.commit()


async def recent_jobs(
    db: AsyncSession, *, cluster: str | None = None, limit: int = 10
) -> list[HpcJobRecordTable]:
    """The researcher's recorded jobs, newest first."""
    query = select(HpcJobRecordTable)
    if cluster:
        query = query.where(HpcJobRecordTable.cluster == cluster)
    query = query.order_by(col(HpcJobRecordTable.submitted_at).desc()).limit(
        max(1, min(limit, 50))
    )
    return list((await db.exec(query)).all())


def describe(jobs: list[HpcJobRecordTable]) -> str:
    """The records as the model reads them, one job a line."""
    if not jobs:
        return "No jobs are recorded for you. (Only jobs submitted from a chat are.)"
    lines = []
    for job in jobs:
        parts = [
            f"job_id: {job.job_id}",
            f"cluster: {job.cluster}",
            f"job: {job.job}",
            f"submitted: {job.submitted_at}",
            f"from: {job.origin}",
        ]
        if job.node_count:
            parts.append(f"nodes: {job.node_count}")
        if job.duration:
            parts.append(f"duration: {job.duration}")
        if job.state:
            parts.append(f"last state: {job.state}")
        lines.append("; ".join(parts))
    return "\n".join(lines)
