"""
Which machine handles Slack: a lease kept in Slack itself.

Two machines with the same app token would each get a share of the events,
so the lease has to be somewhere both can see: a pinned "VISTA status"
message in the bot's direct message with the first linked researcher. Its
metadata names the holder and when it last checked in. Before connecting, and
every minute while connected, an instance reads it over the Web API, which
needs no socket. A holder seen within three minutes keeps it; anyone else
stays disconnected and says who has it. Taking over writes our id, and the old
holder lets go at its next heartbeat. Editing the message notifies no one.

With no linked researcher there is nowhere to pin it, and nothing anyone could
do from Slack but link, so the first instance connects without a lease.
"""

import logging
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import col, select

from ..db.schemas import SlackLinkTable
from ..services import chat_run as chat_run_module
from . import store
from .store import now
from .transport import PinnedMessage, SlackTransport

log = logging.getLogger(__name__)

EVENT_TYPE = "vista_status"
HEARTBEAT = timedelta(seconds=60)
STALE = timedelta(seconds=180)


def host_name() -> str:
    return socket.gethostname() or "unknown host"


@dataclass(frozen=True)
class Holder:
    instance_id: str
    host: str
    heartbeat_at: str
    released: bool = False

    def live(self) -> bool:
        if self.released:
            return False
        try:
            seen = datetime.fromisoformat(self.heartbeat_at)
        except ValueError:
            return False
        return now() - seen < STALE


@dataclass(frozen=True)
class Decision:
    ours: bool
    """ True when this instance holds Slack (or may take it freely). """
    holder: Holder | None
    """ Whoever else is active, when that is why `ours` is False. """


def _holder(message: PinnedMessage) -> Holder | None:
    meta = message.metadata or {}
    if meta.get("event_type") != EVENT_TYPE:
        return None
    payload: dict[str, Any] = meta.get("event_payload") or {}
    try:
        return Holder(
            instance_id=str(payload["instance_id"]),
            host=str(payload.get("host") or "unknown host"),
            heartbeat_at=str(payload["heartbeat_at"]),
            released=bool(payload.get("released", False)),
        )
    except KeyError:
        return None


class Lease:
    def __init__(
        self, slack: SlackTransport, *, instance_id: str, host: str | None = None
    ) -> None:
        self._slack = slack
        self._instance_id = instance_id
        self._host = host or host_name()
        self._ts: str | None = None
        self._channel: str | None = None

    async def _where(self) -> str | None:
        """The DM the status is pinned in: the first linked researcher's."""
        if self._channel is not None:
            return self._channel
        async with chat_run_module.session_factory() as db:
            link = (
                await db.exec(
                    select(SlackLinkTable).order_by(col(SlackLinkTable.created_at))
                )
            ).first()
            if link is None:
                return None
            dm, user = link.dm_channel_id, link.slack_user_id
        self._channel = dm or await self._slack.open_dm(user)
        return self._channel

    async def _read(self) -> tuple[str | None, Holder | None]:
        """The status message's ts and holder; the oldest pin, so all agree."""
        channel = await self._where()
        if channel is None:
            return None, None
        for message in sorted(
            await self._slack.pinned(channel), key=lambda m: float(m.ts)
        ):
            holder = _holder(message)
            if holder is not None:
                return message.ts, holder
        return None, None

    async def check(self) -> Decision:
        """Whether this instance may handle Slack now."""
        ts, holder = await self._read()
        self._ts = ts
        if holder is None or holder.instance_id == self._instance_id:
            decision = Decision(ours=True, holder=None)
        elif holder.live():
            decision = Decision(ours=False, holder=holder)
        else:
            decision = Decision(ours=True, holder=None)
        await self._remember(decision)
        return decision

    async def _write(self, *, released: bool = False) -> None:
        channel = await self._where()
        if channel is None:
            return
        stamp = store.now_iso()
        text = (
            f"VISTA status: handled by {self._host}, last seen <!date^"
            f"{int(now().timestamp())}^{{date_short_pretty}} {{time}}|{stamp}>."
            if not released
            else f"VISTA status: {self._host} has stopped handling Slack."
        )
        metadata = {
            "event_type": EVENT_TYPE,
            "event_payload": {
                "instance_id": self._instance_id,
                "host": self._host,
                "heartbeat_at": stamp,
                "released": released,
            },
        }
        if self._ts is None:
            self._ts = await self._slack.post(channel, text, metadata=metadata)
            await self._slack.pin(channel, self._ts)
        else:
            await self._slack.update(channel, self._ts, text, metadata=metadata)

    async def acquire(self) -> Decision:
        """Check, and if it is ours, claim it. Call before connecting."""
        decision = await self.check()
        if decision.ours:
            await self._write()
        return decision

    async def heartbeat(self) -> bool:
        """Keep the lease. False means someone took over: disconnect."""
        decision = await self.check()
        if not decision.ours:
            log.info(
                "Slack was taken over by %s",
                decision.holder.host if decision.holder else "?",
            )
            return False
        await self._write()
        return True

    async def take_over(self) -> None:
        """Claim it whoever holds it; the holder lets go at its next heartbeat."""
        self._ts, _ = await self._read()
        await self._write()
        await self._remember(Decision(ours=True, holder=None))

    async def release(self) -> None:
        """On a clean stop, so another machine need not wait three minutes."""
        ts, holder = await self._read()
        if holder is not None and holder.instance_id == self._instance_id:
            self._ts = ts
            await self._write(released=True)

    async def _remember(self, decision: Decision) -> None:
        """What the settings page shows: who has Slack, and when last seen."""
        status = {"ours": decision.ours, "checked_at": store.now_iso()}
        if decision.holder is not None:
            status |= {
                "host": decision.holder.host,
                "heartbeat_at": decision.holder.heartbeat_at,
            }
        async with chat_run_module.session_factory() as db:
            config = await store.get_config(db)
            config.lease_status = status
            db.add(config)
            await db.commit()
