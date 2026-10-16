"""
The safeguards against a lost phone: the lock, and limits on Slack jobs.

Both are set only from the desktop app. `lock` sent to the bot turns the lock
on; nothing in Slack turns it off. While it is on, every side effect from
Slack is refused (read-only requests still work). A job submitted from Slack
must also fit the node, walltime and jobs-per-day limits. `refusal` is the
check: the gate runs it before a confirmation is offered, and Confirm runs it
again.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import col, select

from ..db.schemas import HpcJobRecordTable, SlackConfigTable
from ..services import chat_run as chat_run_module
from . import credentials, store

LOCKED = "Slack is locked, so I can't change anything from here. Unlock it in the VISTA desktop app (Settings → Slack)."  # noqa: E501
DAY = timedelta(hours=24)


def minutes(duration: str) -> int | None:
    """`h:mm:ss` (as `submit_hpc_job` takes it) in whole minutes, rounded up."""
    try:
        hours, mins, secs = (int(p) for p in duration.split(":"))
    except ValueError:
        return None
    total = hours * 3600 + mins * 60 + secs
    return -(-total // 60)


def _tail(tool_name: str) -> str:
    return tool_name.split("__")[-1]


async def _jobs_today(user_id: uuid.UUID) -> tuple[int, datetime | None]:
    """Slack jobs in the last 24 hours, and when the oldest leaves the window."""
    since = store.now() - DAY
    async with chat_run_module.session_factory() as db:
        times = (
            await db.exec(
                select(HpcJobRecordTable.submitted_at).where(
                    HpcJobRecordTable.user_id == user_id,
                    HpcJobRecordTable.origin == "slack",
                    col(HpcJobRecordTable.submitted_at)
                    >= since.isoformat(timespec="seconds"),
                )
            )
        ).all()
    if not times:
        return 0, None
    return len(times), datetime.fromisoformat(min(times)) + DAY


def _when(moment: datetime) -> str:
    """A time Slack shows in the reader's own time zone."""
    stamp = int(moment.timestamp())
    fallback = moment.strftime("%H:%M UTC")
    return f"<!date^{stamp}^{{time}}|{fallback}>"


async def check(
    config: SlackConfigTable,
    user_id: uuid.UUID,
    tool_name: str,
    args: dict[str, Any],
) -> str | None:
    """Why this side effect may not go ahead from Slack, or None."""
    if config.locked:
        return LOCKED
    if _tail(tool_name) != "submit_hpc_job":
        return None
    nodes = args.get("node_count")
    if isinstance(nodes, int) and nodes > config.max_nodes:
        return (
            f"{nodes} nodes is over the {config.max_nodes}-node limit for jobs from "
            "Slack. Ask for fewer, or submit it from the desktop app."
        )
    duration = args.get("duration")
    wanted = minutes(duration) if isinstance(duration, str) else None
    if wanted is not None and wanted > config.max_walltime_minutes:
        return (
            f"A walltime of {duration} is over the {config.max_walltime_minutes}-minute "
            "limit for jobs from Slack. Ask for less, or submit it from the desktop app."
        )
    count, resets = await _jobs_today(user_id)
    if count >= config.max_jobs_per_day:
        return (
            f"You've submitted {count} jobs from Slack in the last 24 hours, the "
            f"limit is {config.max_jobs_per_day}. The next one is possible at "
            f"{_when(resets) if resets else 'a later time'}."
        )
    return None


async def refusal(
    user_id: uuid.UUID, tool_name: str, args: dict[str, Any]
) -> str | None:
    """
    The gate's refusal: the lock, then an expired credential for the system,
    then `check` against the saved limits.
    """
    async with chat_run_module.session_factory() as db:
        config = await store.get_config(db)
        snapshot = SlackConfigTable.model_validate(config.model_dump())
        await db.commit()
    if snapshot.locked:
        return LOCKED
    expired = credentials.refusal(user_id, tool_name, args)
    return expired or await check(snapshot, user_id, tool_name, args)


async def lock(slack_user_id: str) -> None:
    """`lock` from Slack. Only the desktop app can undo it."""
    async with chat_run_module.session_factory() as db:
        config = await store.get_config(db)
        if not config.locked:
            config.locked = True
            config.locked_at = store.now_iso()
            config.locked_by = f"slack:{slack_user_id}"
            db.add(config)
        await db.commit()


LOCKED_REPLY = (
    ":lock: Locked. I won't submit, cancel or change anything from Slack until you "
    "unlock me in the VISTA desktop app (Settings → Slack). I can still answer questions."
)
UNLOCK_REPLY = (
    "Unlocking is only possible from the VISTA desktop app (Settings → Slack), so a "
    "lost phone can't undo it."
)
