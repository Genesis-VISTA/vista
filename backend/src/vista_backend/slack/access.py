"""
Who may do what from Slack, decided before any handler runs.

In order: Slack must be on; the workspace must be on the allowlist (anything
else is dropped, logged and never answered); a channel allowlist, when set,
admits only its channels and DMs; `link` is answered for anyone in a DM, since
that is how one becomes linked; everything else needs a linked Slack user.
An unlinked user is told how to link, at most once per
`SlackSettings.unlinked_reply_interval`.

Every event reaching here was verified by the Slack SDK (see `transport.py`).
"""

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import settings
from ..db.schemas import SlackConfigTable, SlackLinkTable, UserTable
from .events import Action, Event, HomeOpened, Message, keyword

log = logging.getLogger("vista.slack.access")

NOT_LINKED = (
    "Hi! I'm VISTA, but your Slack account isn't linked to a VISTA user, so I can't act "
    "for you. To link it, DM me `link` and enter the code I send in the VISTA desktop "
    "app under Settings → Slack."
)
WRONG_CHANNEL = (
    "I don't work in this channel. DM me, or ask in a channel VISTA is set up for."
)
LINK_IN_DM = "To link your account, send `link` to me in a direct message."


@dataclass(frozen=True)
class Linked:
    link: SlackLinkTable
    user: UserTable


Verdict = Literal["drop", "reply", "link", "allow"]


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    """
    drop: do nothing visible. reply: post `reply` and stop. link: handle the
    `link` keyword. allow: the event is from `linked`, go on.
    """
    reply: str | None = None
    linked: Linked | None = None
    ephemeral: bool = False
    """ Post `reply` so only the sender sees it. """


class UnlinkedReplies:
    """When each unlinked Slack user was last told how to link, in this process."""

    def __init__(self) -> None:
        self._last: dict[tuple[str, str], float] = {}

    def due(self, team_id: str, user_id: str) -> bool:
        now = time.monotonic()
        last = self._last.get((team_id, user_id))
        if last is not None and now - last < settings.slack.unlinked_reply_interval:
            return False
        self._last[(team_id, user_id)] = now
        return True


async def find_link(
    session: AsyncSession, team_id: str, slack_user_id: str
) -> Linked | None:
    link = (
        await session.exec(
            select(SlackLinkTable).where(
                SlackLinkTable.team_id == team_id,
                SlackLinkTable.slack_user_id == slack_user_id,
            )
        )
    ).first()
    if link is None:
        return None
    user = await session.get(UserTable, link.user_id)
    if user is None:
        return None
    return Linked(link=link, user=user)


async def links_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> list[SlackLinkTable]:
    return list(
        (
            await session.exec(
                select(SlackLinkTable).where(SlackLinkTable.user_id == user_id)
            )
        ).all()
    )


async def decide(
    session: AsyncSession,
    event: Event,
    config: SlackConfigTable,
    unlinked: UnlinkedReplies,
) -> Decision:
    """What to do with `event`; nothing is posted or written here."""
    if not config.enabled:
        return Decision("drop")
    if event.team_id not in config.allowed_team_ids:
        log.warning(
            "Ignored a Slack event from workspace %s, not on the allowlist.",
            event.team_id,
        )
        return Decision("drop")

    if isinstance(event, HomeOpened):
        linked = await find_link(session, event.team_id, event.user_id)
        return Decision("allow", linked=linked) if linked else Decision("drop")

    is_dm = isinstance(event, Message) and event.is_dm
    if (
        config.allowed_channel_ids
        and not is_dm
        and event.channel_id not in config.allowed_channel_ids
    ):
        if isinstance(event, Message):
            return Decision("reply", reply=WRONG_CHANNEL, ephemeral=True)
        return Decision("drop")

    if isinstance(event, Message) and keyword(event) == "link":
        if not is_dm:
            return Decision("reply", reply=LINK_IN_DM, ephemeral=True)
        return Decision("link")

    linked = await find_link(session, event.team_id, event.user_id)
    if linked is None:
        log.info("Refused a Slack event from unlinked user %s.", event.user_id)
        if isinstance(event, Action):
            # A tap is answered every time: it is on a message they can see.
            return Decision("reply", reply=NOT_LINKED, ephemeral=True)
        if unlinked.due(event.team_id, event.user_id):
            return Decision("reply", reply=NOT_LINKED, ephemeral=not is_dm)
        return Decision("drop")
    return Decision("allow", linked=linked)
