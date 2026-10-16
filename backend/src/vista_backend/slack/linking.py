"""
Linking a Slack user to a VISTA user.

The Slack user DMs the bot `link` and is shown a 6-digit code; the researcher
enters it in the desktop app (`POST /slack/links`). A link therefore needs both
the Slack account and the desktop, never Slack alone. Codes are random, kept
only as a hash, usable once, and expire after `SlackSettings.link_code_ttl`.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import settings
from ..db.schemas import SlackLinkCodeTable, SlackLinkTable
from .store import now, now_iso


class LinkError(ValueError):
    """The code was wrong or expired; the message says which, for the desktop."""


def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


async def issue_code(
    session: AsyncSession, *, team_id: str, slack_user_id: str, dm_channel_id: str
) -> str:
    """A new code for this Slack user, replacing any they had."""
    await session.exec(
        delete(SlackLinkCodeTable).where(
            SlackLinkCodeTable.team_id == team_id,  # type: ignore[arg-type]
            SlackLinkCodeTable.slack_user_id == slack_user_id,  # type: ignore[arg-type]
        )
    )
    await _drop_expired(session)
    expires = now() + timedelta(seconds=settings.slack.link_code_ttl)
    while True:
        code = f"{secrets.randbelow(1_000_000):06d}"
        if await session.get(SlackLinkCodeTable, _hash(code)) is None:
            break
    session.add(
        SlackLinkCodeTable(
            code_hash=_hash(code),
            team_id=team_id,
            slack_user_id=slack_user_id,
            dm_channel_id=dm_channel_id,
            expires_at=expires.isoformat(timespec="seconds"),
        )
    )
    await session.commit()
    return code


def code_message(code: str) -> str:
    minutes = round(settings.slack.link_code_ttl / 60)
    return (
        f"Your link code is *{code}*. Enter it in the VISTA desktop app under "
        f"Settings → Slack within {minutes} minutes. Don't share it."
    )


async def _drop_expired(session: AsyncSession) -> None:
    await session.exec(
        delete(SlackLinkCodeTable).where(
            SlackLinkCodeTable.expires_at < now_iso()  # type: ignore[arg-type]
        )
    )


async def redeem_code(
    session: AsyncSession, *, code: str, user_id: uuid.UUID
) -> SlackLinkTable:
    """Link the Slack user the code was issued to, to `user_id`. One use only."""
    pending = await session.get(SlackLinkCodeTable, _hash(code))
    if pending is None:
        raise LinkError("That code isn't right. DM the VISTA bot `link` for a new one.")
    await session.delete(pending)
    if datetime.fromisoformat(pending.expires_at) < now():
        await session.commit()
        raise LinkError("That code has expired. DM the VISTA bot `link` for a new one.")

    link = (
        await session.exec(
            select(SlackLinkTable).where(
                SlackLinkTable.team_id == pending.team_id,
                SlackLinkTable.slack_user_id == pending.slack_user_id,
            )
        )
    ).first()
    if link is None:
        link = SlackLinkTable(
            team_id=pending.team_id,
            slack_user_id=pending.slack_user_id,
            user_id=user_id,
            created_at=now_iso(),
        )
    link.user_id = user_id
    link.dm_channel_id = pending.dm_channel_id
    session.add(link)
    await session.commit()
    await session.refresh(link)
    return link
