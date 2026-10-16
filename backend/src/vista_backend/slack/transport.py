"""
How VISTA's Slack code reaches Slack, and how Slack reaches it.

Handlers depend only on `SlackTransport`. The one implementation is Socket Mode
(`socket_mode.py`): an outbound WebSocket, so a machine with no public address
can serve Slack. A hosted relay would be a second implementation of the same
interface (`docs/slack-relay.md`); tests use `tests/slack/fakes.FakeTransport`.

An implementation verifies and acknowledges every event before passing it on,
within Slack's 3-second deadline, and retries rate-limited calls as Slack asks.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .events import Event

Handler = Callable[[Event], Awaitable[None]]


class SlackCallError(RuntimeError):
    """A Slack Web API call failed; `error` is Slack's code, e.g. `msg_too_long`."""

    def __init__(self, method: str, error: str) -> None:
        super().__init__(f"Slack {method} failed: {error}")
        self.method = method
        self.error = error


@dataclass(frozen=True)
class Identity:
    """Who the bot is, from `auth.test`."""

    team_id: str
    bot_user_id: str


@dataclass(frozen=True)
class PinnedMessage:
    ts: str
    text: str
    metadata: dict[str, Any] | None
    """ Slack message metadata: `{"event_type": ..., "event_payload": {...}}`. """


class SlackTransport(ABC):
    @abstractmethod
    async def identity(self) -> Identity:
        """Who the bot is. Works without a connection."""

    @abstractmethod
    async def connect(self, handler: Handler) -> None:
        """Start receiving events, each passed to `handler` after it is acknowledged."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Stop receiving events. Web API calls keep working."""

    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @abstractmethod
    async def post(
        self,
        channel: str,
        text: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
        thread_ts: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Post a message and return its `ts`."""

    @abstractmethod
    async def update(
        self,
        channel: str,
        ts: str,
        text: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Replace a message the bot posted. Sends no notification."""

    @abstractmethod
    async def ephemeral(
        self, channel: str, user: str, text: str, *, thread_ts: str | None = None
    ) -> None:
        """A message only `user` sees."""

    @abstractmethod
    async def upload(
        self,
        channel: str,
        *,
        filename: str,
        content: bytes,
        title: str,
        thread_ts: str | None = None,
    ) -> None:
        """Attach a file to a channel or thread."""

    @abstractmethod
    async def open_dm(self, user: str) -> str:
        """The bot's direct-message channel with `user`."""

    @abstractmethod
    async def publish_home(self, user: str, view: dict[str, Any]) -> None:
        """Replace `user`'s App Home tab."""

    @abstractmethod
    async def pin(self, channel: str, ts: str) -> None: ...

    @abstractmethod
    async def pinned(self, channel: str) -> list[PinnedMessage]:
        """The bot's pinned messages in `channel`, with their metadata."""
