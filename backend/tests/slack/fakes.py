"""
A Slack transport that records what VISTA posts and lets a test deliver events.

Stands in for Socket Mode in every Slack test, so nothing reaches Slack.
"""

import itertools
from dataclasses import dataclass, field
from typing import Any

from vista_backend.slack.events import Event
from vista_backend.slack.transport import (
    Handler,
    Identity,
    PinnedMessage,
    SlackCallError,
    SlackTransport,
)


@dataclass
class Posted:
    channel: str
    ts: str
    text: str
    blocks: list[dict[str, Any]] | None = None
    thread_ts: str | None = None
    metadata: dict[str, Any] | None = None
    edits: list[str] = field(default_factory=list)
    """ Each text this message was updated to, in order. """


class FakeTransport(SlackTransport):
    def __init__(self, *, team_id: str = "T0001", bot_user_id: str = "UBOT") -> None:
        self._identity = Identity(team_id=team_id, bot_user_id=bot_user_id)
        self._handler: Handler | None = None
        self._ts = itertools.count(1)
        self.messages: list[Posted] = []
        self.ephemerals: list[tuple[str, str, str]] = []
        """ (channel, user, text). """
        self.uploads: list[dict[str, Any]] = []
        self.homes: dict[str, dict[str, Any]] = {}
        self.pins: dict[str, list[str]] = {}
        self.fail_next: dict[str, str] = {}
        """ method → Slack error code, raised once by the next such call. """
        self.connects = 0
        self.disconnects = 0

    def _maybe_fail(self, method: str) -> None:
        error = self.fail_next.pop(method, None)
        if error is not None:
            raise SlackCallError(method, error)

    # -- intake ---------------------------------------------------------------

    async def deliver(self, event: Event) -> None:
        """What the real transport does after acking: hand the event to VISTA."""
        assert self._handler is not None, "not connected"
        await self._handler(event)

    async def identity(self) -> Identity:
        return self._identity

    async def connect(self, handler: Handler) -> None:
        self._handler = handler
        self.connects += 1

    async def disconnect(self) -> None:
        self._handler = None
        self.disconnects += 1

    @property
    def connected(self) -> bool:
        return self._handler is not None

    # -- Web API --------------------------------------------------------------

    def find(self, ts: str) -> Posted:
        return next(m for m in self.messages if m.ts == ts)

    def in_channel(self, channel: str) -> list[Posted]:
        return [m for m in self.messages if m.channel == channel]

    async def post(
        self, channel, text, *, blocks=None, thread_ts=None, metadata=None
    ) -> str:
        self._maybe_fail("post")
        ts = f"{next(self._ts)}.000"
        self.messages.append(Posted(channel, ts, text, blocks, thread_ts, metadata))
        return ts

    async def update(self, channel, ts, text, *, blocks=None, metadata=None) -> None:
        self._maybe_fail("update")
        message = self.find(ts)
        message.text = text
        message.blocks = blocks
        if metadata is not None:
            message.metadata = metadata
        message.edits.append(text)

    async def ephemeral(self, channel, user, text, *, thread_ts=None) -> None:
        self._maybe_fail("ephemeral")
        self.ephemerals.append((channel, user, text))

    async def upload(
        self, channel, *, filename, content, title, thread_ts=None
    ) -> None:
        self._maybe_fail("upload")
        self.uploads.append(
            {
                "channel": channel,
                "filename": filename,
                "content": content,
                "title": title,
                "thread_ts": thread_ts,
            }
        )

    async def open_dm(self, user: str) -> str:
        return f"D{user}"

    async def publish_home(self, user, view) -> None:
        self._maybe_fail("publish_home")
        self.homes[user] = view

    async def pin(self, channel, ts) -> None:
        self.pins.setdefault(channel, [])
        if ts not in self.pins[channel]:
            self.pins[channel].append(ts)

    async def pinned(self, channel) -> list[PinnedMessage]:
        return [
            PinnedMessage(ts=m.ts, text=m.text, metadata=m.metadata)
            for ts in self.pins.get(channel, [])
            for m in [self.find(ts)]
        ]
