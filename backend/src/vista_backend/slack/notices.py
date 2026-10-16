"""
Going offline and coming back: the notices in each linked researcher's DM.

A message sent to VISTA while it is off is lost, so a researcher is told when
it goes: on a clean stop, a quit, or before the system sleeps. A crash gives no
notice. Back online is said only after an offline notice, and never sooner
than two minutes after it: a quick restart says nothing until the two minutes
are up, and then only if VISTA is still here. A dropped connection that never
sent an offline notice says nothing at all.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlmodel import select

from ..db.schemas import SlackLinkTable
from ..services import chat_run as chat_run_module
from . import store
from .lease import host_name
from .store import now
from .transport import SlackTransport

log = logging.getLogger(__name__)

DEBOUNCE = timedelta(minutes=2)
SHUTDOWN_BUDGET = 3.0
"""Seconds a stop may spend telling people; Slack being slow must not hang it."""


def offline_text(host: str) -> str:
    return (
        f":zzz: Your VISTA agent is going offline (host: {host}). "
        "Messages sent while it's offline won't be received."
    )


ONLINE_TEXT = ":large_green_circle: VISTA is back online."


class Notices:
    def __init__(self, slack: SlackTransport, *, host: str | None = None) -> None:
        self._slack = slack
        self._host = host or host_name()
        self._pending: asyncio.Task | None = None

    async def _channels(self) -> list[str]:
        async with chat_run_module.session_factory() as db:
            links = (await db.exec(select(SlackLinkTable))).all()
            pairs = [(link.dm_channel_id, link.slack_user_id) for link in links]
        return [dm or await self._slack.open_dm(user) for dm, user in pairs]

    async def _tell_all(self, text: str) -> int:
        sent = 0
        for channel in await self._channels():
            try:
                await self._slack.post(channel, text)
                sent += 1
            except Exception:
                log.warning("Could not post a notice to %s", channel)
        return sent

    async def going_offline(self) -> None:
        """Before a stop or sleep. Bounded, so it never holds the stop up."""
        self.cancel()
        try:
            await asyncio.wait_for(self._offline(), timeout=SHUTDOWN_BUDGET)
        except TimeoutError:
            log.warning("The offline notice did not finish in time")

    async def _offline(self) -> None:
        if await self._tell_all(offline_text(self._host)) == 0:
            return
        async with chat_run_module.session_factory() as db:
            config = await store.get_config(db)
            config.offline_notice_sent_at = now().isoformat()
            db.add(config)
            await db.commit()

    async def connected(self) -> None:
        """On every connect: arrange back online if an offline notice went out."""
        async with chat_run_module.session_factory() as db:
            config = await store.get_config(db)
            sent_at = config.offline_notice_sent_at
            config.online_since = store.now_iso()
            db.add(config)
            await db.commit()
        if sent_at is None:
            return
        try:
            wait = (datetime.fromisoformat(sent_at) + DEBOUNCE - now()).total_seconds()
        except ValueError:
            wait = 0.0
        self.cancel()
        self._pending = asyncio.create_task(self._back_online(sent_at, max(0.0, wait)))

    async def _back_online(self, sent_at: str, wait: float) -> None:
        if wait:
            await asyncio.sleep(wait)
        async with chat_run_module.session_factory() as db:
            config = await store.get_config(db)
            if config.offline_notice_sent_at != sent_at:
                await db.commit()
                return  # Gone offline again since, or already said.
            config.offline_notice_sent_at = None
            config.last_online_notice_at = store.now_iso()
            db.add(config)
            await db.commit()
        await self._tell_all(ONLINE_TEXT)

    def cancel(self) -> None:
        """A disconnect before the back-online message went out."""
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()
        self._pending = None

    async def wait(self) -> None:
        """For tests: let a pending back-online message go out."""
        if self._pending is not None:
            await self._pending
