"""
The safeguards against a lost phone: the lock, and limits on Slack jobs.

Both are set only from the desktop app. `lock` sent to the bot turns the lock
on; nothing in Slack turns it off. While it is on, every side effect from
Slack is refused (read-only requests still work). A job submitted from Slack
must also fit the node, walltime and jobs-per-day limits. `refusal` is the
check: the gate runs it before a confirmation is offered, and Confirm runs it
again.

Nodes and walltime are those `preview_hpc_job` resolves, the job's defaults
included: a model that leaves them out must not get past the limit with a
default that is over it. An `agenthpc` parameter set's resources are fixed by
its application's batch script on the cluster, which VISTA cannot read, so for
it only the lock and the jobs-per-day limit apply.
"""

import logging
import re
import uuid

from datetime import datetime, timedelta
from typing import Any

from sqlmodel import col, select

from ..db.schemas import HpcJobRecordTable, SlackConfigTable
from ..services import chat_run as chat_run_module
from . import confirm, credentials, store

log = logging.getLogger(__name__)

SUBMITS = frozenset({"submit_hpc_job", "agenthpc_submit_parameter_set"})
_SECONDS = re.compile(r"\((\d+)s\)")

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


async def _jobs_today() -> tuple[int, datetime | None]:
    """
    Slack jobs in the last 24 hours, and when the oldest leaves the window.
    Every linked Slack account acts as this install's one researcher, so the
    limit is the install's.
    """
    since = store.now() - DAY
    async with chat_run_module.session_factory() as db:
        times = (
            await db.exec(
                select(HpcJobRecordTable.submitted_at).where(
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


def resolved(fields: dict[str, str]) -> tuple[int | None, int | None, str | None]:
    """(nodes, walltime in minutes, walltime as shown) from `preview_hpc_job`'s card fields."""
    nodes = fields.get("Nodes", "")
    walltime = fields.get("Walltime", "")
    seconds = _SECONDS.search(walltime)
    return (
        int(nodes) if nodes.strip().isdigit() else None,
        -(-int(seconds.group(1)) // 60) if seconds else None,
        walltime.split(" (")[0] or None,
    )


async def check(
    config: SlackConfigTable,
    tool_name: str,
    args: dict[str, Any],
    preview: dict[str, str] | None = None,
) -> str | None:
    """
    Why this side effect may not go ahead from Slack, or None. `preview` is
    `preview_hpc_job`'s card fields for a `submit_hpc_job`; without it the
    model's own arguments are all there is to check.
    """
    if config.locked:
        return LOCKED
    if _tail(tool_name) not in SUBMITS:
        return None
    if _tail(tool_name) == "submit_hpc_job":
        nodes, wanted, shown = resolved(preview or {})
        if nodes is None and isinstance(args.get("node_count"), int):
            nodes = args["node_count"]
        duration = args.get("duration")
        if wanted is None and isinstance(duration, str):
            wanted, shown = minutes(duration), duration
        if nodes is not None and nodes > config.max_nodes:
            return (
                f"{nodes} nodes is over the {config.max_nodes}-node limit for jobs from "
                "Slack. Ask for fewer, or submit it from the desktop app."
            )
        if wanted is not None and wanted > config.max_walltime_minutes:
            return (
                f"A walltime of {shown} is over the {config.max_walltime_minutes}-minute "
                "limit for jobs from Slack. Ask for less, or submit it from the desktop app."
            )
    count, resets = await _jobs_today()
    if count >= config.max_jobs_per_day:
        return (
            f"You've submitted {count} jobs from Slack in the last 24 hours, the "
            f"limit is {config.max_jobs_per_day}. The next one is possible at "
            f"{_when(resets) if resets else 'a later time'}."
        )
    return None


async def refusal(
    tool_name: str,
    args: dict[str, Any],
    *,
    chat_session_id: uuid.UUID | None = None,
) -> str | None:
    """
    The gate's refusal: the lock, then an expired credential for the system,
    then `check` against the saved limits, with what `preview_hpc_job` resolves
    for the conversation's project.
    """
    async with chat_run_module.session_factory() as db:
        config = await store.get_config(db)
        snapshot = SlackConfigTable.model_validate(config.model_dump())
        await db.commit()
    if snapshot.locked:
        return LOCKED
    expired = credentials.refusal(tool_name, args)
    if expired:
        return expired
    preview: dict[str, str] = {}
    if _tail(tool_name) == "submit_hpc_job" and chat_session_id is not None:
        try:
            preview = await confirm.preview(chat_session_id, tool_name, args)
        except Exception as exc:  # A job that does not exist: the tool will say so.
            log.info("No preview for the Slack limits: %s", exc)
    return await check(snapshot, tool_name, args, preview)


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
