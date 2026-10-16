"""
What was done from Slack, and on whose word: `slack_audit` rows, mirrored as
lines of `<data_dir>/logs/slack-audit.jsonl` so they can be read without VISTA.

A record is one event: a request, a refusal, a confirmation card posted and
how it closed, and a tool's result with the job id it reported. Everything
passes through `redact` first, so no credential reaches the log even when one
was pasted into Slack or sits in a tool's arguments. Writing the log never
stops the action it records: a failure is logged and the action goes on.
"""

import json
import logging
import re
import uuid
from typing import Any, Literal

from ..config import settings
from ..db.schemas import ChatSessionTable, SlackAuditTable
from ..services import chat_run as chat_run_module
from .store import now_iso

log = logging.getLogger(__name__)

Event = Literal[
    "request",
    "refused",
    "confirm_posted",
    "confirmed",
    "cancelled",
    "expired",
    "tool_result",
]

REDACTED = "[redacted]"
SECRET_KEY = re.compile(r"token|password|passwd|secret|passcode|otp|mfa", re.I)
SECRET_VALUE = re.compile(
    r"xox[abposr]-[A-Za-z0-9-]+"  # Slack tokens
    r"|xapp-[A-Za-z0-9-]+"
    r"|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"  # JWTs
    r"|(?i:bearer)\s+[A-Za-z0-9._~+/=-]+"
)
TEXT_LIMIT = 2000
""" Characters of a request kept; the conversation itself holds the rest. """


def log_path():
    return settings.data_dir / "logs" / "slack-audit.jsonl"


def redact(value: Any) -> Any:
    """`value` with credential-named keys dropped and token-shaped text masked."""
    if isinstance(value, dict):
        return {
            str(k): REDACTED if SECRET_KEY.search(str(k)) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return SECRET_VALUE.sub(REDACTED, value)
    if value is None or isinstance(value, bool | int | float):
        return value
    return redact(str(value))


async def record(
    event: Event,
    *,
    team_id: str | None = None,
    slack_user_id: str | None = None,
    user_id: uuid.UUID | None = None,
    chat_session_id: uuid.UUID | None = None,
    tool_name: str | None = None,
    detail: dict[str, Any] | None = None,
    job_id: str | None = None,
) -> None:
    try:
        async with chat_run_module.session_factory() as db:
            if user_id is None and chat_session_id is not None:
                chat = await db.get(ChatSessionTable, chat_session_id)
                user_id = chat.user_id if chat is not None else None
            row = SlackAuditTable(
                at=now_iso(),
                team_id=team_id,
                slack_user_id=slack_user_id,
                user_id=user_id,
                chat_session_id=chat_session_id,
                event=event,
                tool_name=tool_name,
                detail=redact(detail) if detail is not None else None,
                job_id=job_id,
            )
            line = json.dumps(
                {
                    "at": row.at,
                    "event": row.event,
                    "team_id": row.team_id,
                    "slack_user_id": row.slack_user_id,
                    "user_id": str(row.user_id) if row.user_id else None,
                    "chat_session_id": (
                        str(row.chat_session_id) if row.chat_session_id else None
                    ),
                    "tool_name": row.tool_name,
                    "detail": row.detail,
                    "job_id": row.job_id,
                }
            )
            db.add(row)
            await db.commit()
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        log.exception("Could not write the Slack audit record for %s", event)


async def request(
    *,
    team_id: str,
    slack_user_id: str,
    user_id: uuid.UUID,
    chat_session_id: uuid.UUID | None,
    channel_id: str,
    thread_ts: str,
    text: str,
) -> None:
    """A message from Slack that VISTA acted on."""
    await record(
        "request",
        team_id=team_id,
        slack_user_id=slack_user_id,
        user_id=user_id,
        chat_session_id=chat_session_id,
        detail={
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "text": text[:TEXT_LIMIT],
        },
    )
