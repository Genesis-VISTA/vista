"""
Slack events as VISTA's Slack code sees them, after the transport has verified
and acknowledged them. Independent of Bolt, so handlers and tests need neither.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Literal

_MENTION = re.compile(r"<@[A-Z0-9]+>")


@dataclass(frozen=True)
class Message:
    """A direct message to the bot, or a message that mentions it."""

    team_id: str
    channel_id: str
    user_id: str
    text: str
    ts: str
    thread_ts: str | None = None
    """ The thread it was posted in; None for a top-level message. """
    is_dm: bool = False
    files: list[dict[str, Any]] = field(default_factory=list)

    @property
    def root_ts(self) -> str:
        """The thread this message belongs to, or starts."""
        return self.thread_ts or self.ts

    @property
    def command(self) -> str:
        """The text without mentions, trimmed and lower-cased, for keywords."""
        return _MENTION.sub("", self.text).strip().lower()

    @property
    def prompt(self) -> str:
        """The text without the bot's mention, as the agent should read it."""
        return _MENTION.sub("", self.text).strip()


@dataclass(frozen=True)
class Action:
    """A button tapped on one of VISTA's messages."""

    team_id: str
    channel_id: str
    user_id: str
    action_id: str
    value: str
    message_ts: str
    thread_ts: str | None = None


@dataclass(frozen=True)
class HomeOpened:
    team_id: str
    user_id: str


Event = Message | Action | HomeOpened

Keyword = Literal["link", "lock", "unlock"]


def keyword(message: Message) -> Keyword | None:
    command = message.command
    if command in ("link", "lock", "unlock"):
        return command  # type: ignore[return-value]
    return None
