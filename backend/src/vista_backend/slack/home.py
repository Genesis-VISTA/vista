"""
The bot's Home tab: is VISTA up, on which machine, and what is it doing.

Slack keeps the last view published, so a Home tab seen while VISTA is off
shows how things stood when it was last on. Every view says "as of" when, so
a stale one is plain; and a "going offline" view is published on a clean stop.
It is published when the tab is opened, after a lock or link change or a job
ending, and every five minutes.
"""

import logging
from datetime import datetime
from typing import Any

from sqlmodel import col, select

from ..db.schemas import HpcJobRecordTable, SlackConfigTable, SlackLinkTable
from ..services import chat_run as chat_run_module
from . import credentials, store
from .credentials import Expiry
from .format import context, section
from .lease import host_name
from .transport import SlackTransport

log = logging.getLogger(__name__)

INTERVAL = 5 * 60.0
RECENT = 5


def _when(iso: str | None) -> str:
    if not iso:
        return "unknown"
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return f"<!date^{int(moment.timestamp())}^{{date_short_pretty}} {{time}}|{iso}>"


def view(
    *,
    online: bool,
    host: str,
    config: SlackConfigTable,
    expiries: list[Expiry],
    jobs: list[HpcJobRecordTable],
    as_of: str,
) -> dict[str, Any]:
    """The Home tab's blocks. Pure, so a test can pin them."""
    if online:
        status = f":large_green_circle: *Online* on `{host}` since {_when(config.online_since)}"
    else:
        status = f":zzz: *Offline*: last seen on `{host}` at {_when(as_of)}. Messages sent now won't be received."
    lock = (
        f":lock: *Locked* since {_when(config.locked_at)}: nothing can be changed from Slack until it is unlocked in the desktop app."
        if config.locked
        else ":unlock: Not locked. Send `lock` to block changes from Slack."
    )
    if expiries:
        lines = []
        for e in sorted(expiries, key=lambda e: e.expires_at):
            gone = e.expires_at <= store.now()
            estimated = " (estimated)" if e.estimated else ""
            verb = "expired" if gone else "expires"
            lines.append(
                f"• {e.label()}: {verb} {_when(e.expires_at.isoformat())}{estimated}"
            )
        creds = "*HPC access*\n" + "\n".join(lines)
    else:
        creds = "*HPC access*\nNo expiry is known for your credentials."
    if jobs:
        rows = [
            f"• `{j.job_id}` {j.job} on {j.cluster}: {j.state or 'submitted'}, {_when(j.submitted_at)}"
            for j in jobs
        ]
        recent = "*Recent jobs from Slack*\n" + "\n".join(rows)
    else:
        recent = "*Recent jobs from Slack*\nNone yet."
    limits = (
        f"Slack limits: {config.max_nodes} nodes, {config.max_walltime_minutes} min "
        f"walltime, {config.max_jobs_per_day} jobs a day. Set in the desktop app."
    )
    return {
        "type": "home",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "VISTA"}},
            section(status),
            section(lock),
            {"type": "divider"},
            section(creds),
            {"type": "divider"},
            section(recent),
            context(f"{limits} As of {_when(as_of)}."),
        ],
    }


class Home:
    def __init__(self, slack: SlackTransport, *, host: str | None = None) -> None:
        self._slack = slack
        self._host = host or host_name()

    async def publish(
        self, team_id: str, slack_user_id: str, *, online: bool = True
    ) -> bool:
        """One researcher's Home tab. False when they are not linked."""
        async with chat_run_module.session_factory() as db:
            link = (
                await db.exec(
                    select(SlackLinkTable).where(
                        SlackLinkTable.team_id == team_id,
                        SlackLinkTable.slack_user_id == slack_user_id,
                    )
                )
            ).first()
            if link is None:
                await db.commit()
                return False
            user_id = link.user_id
            config = await store.get_config(db)
            snapshot = SlackConfigTable.model_validate(config.model_dump())
            jobs = [
                HpcJobRecordTable.model_validate(j.model_dump())
                for j in (
                    await db.exec(
                        select(HpcJobRecordTable)
                        .where(
                            HpcJobRecordTable.user_id == user_id,
                            HpcJobRecordTable.origin == "slack",
                        )
                        .order_by(col(HpcJobRecordTable.submitted_at).desc())
                        .limit(RECENT)
                    )
                ).all()
            ]
            await db.commit()
        await self._slack.publish_home(
            slack_user_id,
            view(
                online=online,
                host=self._host,
                config=snapshot,
                expiries=credentials.known(user_id),
                jobs=jobs,
                as_of=store.now_iso(),
            ),
        )
        return True

    async def publish_all(self, *, online: bool = True) -> int:
        async with chat_run_module.session_factory() as db:
            pairs = [
                (link.team_id, link.slack_user_id)
                for link in (await db.exec(select(SlackLinkTable))).all()
            ]
        done = 0
        for team_id, slack_user_id in pairs:
            try:
                done += await self.publish(team_id, slack_user_id, online=online)
            except Exception:
                log.warning("Could not publish a Home tab")
        return done
