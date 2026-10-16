"""
The Socket Mode transport: Slack Bolt over an outbound WebSocket.

Bolt checks the app-level token on connect and drops the bot's own events, and
every request is acknowledged before VISTA's handler runs, in a task of its
own, so a slow turn never misses Slack's 3-second deadline. Web API calls go
through the SDK's rate-limit handler, which waits as long as a 429 asks.
"""

import asyncio
import logging
import re
from typing import Any

from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
from slack_bolt.app.async_app import AsyncApp
from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry.builtin_async_handlers import (
    AsyncConnectionErrorRetryHandler,
    AsyncRateLimitErrorRetryHandler,
)
from slack_sdk.web.async_client import AsyncWebClient

from .events import Action, Event, HomeOpened, Message
from .transport import Handler, Identity, PinnedMessage, SlackCallError, SlackTransport

log = logging.getLogger("vista.slack")

# Message subtypes that are a person writing to the bot. Edits, deletions,
# joins and bot posts are not.
_HUMAN_SUBTYPES = {None, "file_share", "thread_broadcast"}


def web_client(bot_token: str) -> AsyncWebClient:
    return AsyncWebClient(
        token=bot_token,
        retry_handlers=[
            AsyncRateLimitErrorRetryHandler(max_retry_count=5),
            AsyncConnectionErrorRetryHandler(max_retry_count=2),
        ],
    )


def message_event(body: dict[str, Any], event: dict[str, Any]) -> Message | None:
    """A Slack `message` or `app_mention` event as a `Message`, or None to ignore it."""
    if event.get("subtype") not in _HUMAN_SUBTYPES or event.get("bot_id"):
        return None
    user = event.get("user")
    channel = event.get("channel")
    if not user or not channel:
        return None
    return Message(
        team_id=body.get("team_id") or event.get("team") or "",
        channel_id=channel,
        user_id=user,
        text=event.get("text") or "",
        ts=event["ts"],
        thread_ts=event.get("thread_ts"),
        is_dm=event.get("channel_type") == "im",
        files=list(event.get("files") or []),
    )


def action_event(body: dict[str, Any]) -> Action | None:
    actions = body.get("actions") or []
    if not actions:
        return None
    action = actions[0]
    container = body.get("container") or {}
    message = body.get("message") or {}
    return Action(
        team_id=(body.get("team") or {}).get("id") or body.get("team_id") or "",
        channel_id=(body.get("channel") or {}).get("id")
        or container.get("channel_id", ""),
        user_id=(body.get("user") or {}).get("id", ""),
        action_id=action.get("action_id", ""),
        value=action.get("value", ""),
        message_ts=container.get("message_ts") or message.get("ts", ""),
        thread_ts=container.get("thread_ts") or message.get("thread_ts"),
    )


class SocketModeTransport(SlackTransport):
    def __init__(self, *, bot_token: str, app_token: str) -> None:
        self._app_token = app_token
        self.client = web_client(bot_token)
        self._handler: Handler | None = None
        self._socket: AsyncSocketModeHandler | None = None
        self._tasks: set[asyncio.Task] = set()
        self._identity: Identity | None = None
        self.app = self._build_app()

    # -- intake ---------------------------------------------------------------

    def _dispatch(self, event: Event | None) -> None:
        """Run the handler after the ack, never in Slack's 3-second window."""
        if event is None or self._handler is None:
            return
        task = asyncio.create_task(self._run(self._handler, event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _run(handler: Handler, event: Event) -> None:
        try:
            await handler(event)
        except Exception:
            log.exception("Slack handler failed for %s", type(event).__name__)

    def _build_app(self) -> AsyncApp:
        app = AsyncApp(client=self.client)

        @app.event("message")
        async def on_message(body, event, ack):
            await ack()
            # Mentions in channels arrive as `app_mention`; this handles DMs.
            if event.get("channel_type") == "im":
                self._dispatch(message_event(body, event))

        @app.event("app_mention")
        async def on_mention(body, event, ack):
            await ack()
            self._dispatch(message_event(body, event))

        @app.event("app_home_opened")
        async def on_home(body, event, ack):
            await ack()
            if event.get("tab") == "home":
                self._dispatch(
                    HomeOpened(team_id=body.get("team_id", ""), user_id=event["user"])
                )

        @app.action(re.compile(r"^vista_"))
        async def on_action(body, ack):
            await ack()
            self._dispatch(action_event(body))

        return app

    async def identity(self) -> Identity:
        if self._identity is None:
            auth = await self._call("auth_test")
            self._identity = Identity(
                team_id=auth["team_id"], bot_user_id=auth["user_id"]
            )
        return self._identity

    async def connect(self, handler: Handler) -> None:
        self._handler = handler
        self._socket = AsyncSocketModeHandler(self.app, self._app_token)
        await self._socket.connect_async()
        log.info("Connected to Slack (Socket Mode).")

    async def disconnect(self) -> None:
        socket, self._socket = self._socket, None
        self._handler = None
        if socket is not None:
            await socket.close_async()
            log.info("Disconnected from Slack.")

    @property
    def connected(self) -> bool:
        return self._socket is not None

    # -- Web API --------------------------------------------------------------

    async def _call(self, method: str, **kwargs: Any) -> Any:
        try:
            return await getattr(self.client, method)(**kwargs)
        except SlackApiError as exc:
            raise SlackCallError(method, str(exc.response.get("error", exc))) from exc

    async def post(
        self, channel, text, *, blocks=None, thread_ts=None, metadata=None
    ) -> str:
        response = await self._call(
            "chat_postMessage",
            channel=channel,
            text=text,
            blocks=blocks,
            thread_ts=thread_ts,
            metadata=metadata,
            unfurl_links=False,
            unfurl_media=False,
        )
        return response["ts"]

    async def update(self, channel, ts, text, *, blocks=None, metadata=None) -> None:
        await self._call(
            "chat_update",
            channel=channel,
            ts=ts,
            text=text,
            blocks=blocks or [],
            metadata=metadata,
        )

    async def ephemeral(self, channel, user, text, *, thread_ts=None) -> None:
        await self._call(
            "chat_postEphemeral",
            channel=channel,
            user=user,
            text=text,
            thread_ts=thread_ts,
        )

    async def upload(
        self, channel, *, filename, content, title, thread_ts=None
    ) -> None:
        await self._call(
            "files_upload_v2",
            channel=channel,
            filename=filename,
            content=content,
            title=title,
            thread_ts=thread_ts,
        )

    async def open_dm(self, user: str) -> str:
        response = await self._call("conversations_open", users=user)
        return response["channel"]["id"]

    async def publish_home(self, user, view) -> None:
        await self._call("views_publish", user_id=user, view=view)

    async def pin(self, channel, ts) -> None:
        try:
            await self._call("pins_add", channel=channel, timestamp=ts)
        except SlackCallError as exc:
            if exc.error != "already_pinned":
                raise

    async def pinned(self, channel) -> list[PinnedMessage]:
        bot = (await self.identity()).bot_user_id
        listing = await self._call("pins_list", channel=channel)
        out: list[PinnedMessage] = []
        for item in listing.get("items") or []:
            message = item.get("message") or {}
            if message.get("user") != bot:
                continue
            # `pins.list` leaves metadata out; the message itself carries it.
            history = await self._call(
                "conversations_history",
                channel=channel,
                latest=message["ts"],
                oldest=message["ts"],
                inclusive=True,
                limit=1,
                include_all_metadata=True,
            )
            found = (history.get("messages") or [message])[0]
            out.append(
                PinnedMessage(
                    ts=found["ts"],
                    text=found.get("text", ""),
                    metadata=found.get("metadata"),
                )
            )
        return out
