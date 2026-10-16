"""
A record of every HPC job a researcher submits through a chat, from any channel.

`SubmitWatcher` sits in a chat run and, when `submit_hpc_job` returns a
summary, writes an `hpc_job_record` row at once: the cluster and job id it
reported, what was asked for, and, for a Slack turn, the thread to report back
to. `list_my_recent_jobs` reads them, so "my last job" means the last job this
researcher submitted, not whatever the model remembers. Campaign jobs keep
their own table and are not recorded here.
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
from ..db.schemas import HpcJobRecordTable, SlackThreadTable

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
        user_id: uuid.UUID,
        chat_session_id: uuid.UUID,
        channel: str,
    ) -> None:
        self._session_factory = session_factory
        self._user_id = user_id
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
                db.add(
                    HpcJobRecordTable(
                        user_id=self._user_id,
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
            from ..slack import audit  # Here, as it imports the chat run.

            await audit.record(
                "tool_result",
                user_id=self._user_id,
                chat_session_id=self._chat_session_id,
                tool_name=SUBMIT,
                detail={"args": args, "cluster": found["cluster"]},
                job_id=found["job_id"],
            )


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


async def recent_jobs(
    db: AsyncSession, user_id: uuid.UUID, *, cluster: str | None = None, limit: int = 10
) -> list[HpcJobRecordTable]:
    """The researcher's recorded jobs, newest first."""
    query = select(HpcJobRecordTable).where(HpcJobRecordTable.user_id == user_id)
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
