"""
The Slack service: everything in `slack/` put together and run with the backend.

`SlackService.start` does nothing unless Slack is on and both tokens are in
the secret store. Then, in order: install the gate's refusal and the card's
preview, expire cards left open by the last run, connect, follow every run of
a Slack conversation in its thread, and run the background loops —
credential reminders and the App Home. A job's end reaches its thread through
its watch (`services/job_records`), not a loop here. `stop` marks the App Home
offline, then undoes all of it.

A Slack message is put in its conversation's inbox, like a message sent in the
app, and wakes the conversation; there is no other way into a turn. One that
waits behind a running turn gets ⏳, swapped for ✅ when a turn takes it. A new
thread from someone with several projects and none chosen for Slack is first
asked which project; its messages wait for the tap.

Slack runs on one machine at a time: two connected with the same app token
would each get a share of the events (`docs/slack.md`).

Events arrive here already verified and acknowledged by the transport. Each
passes `access.decide` before anything else looks at it.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from ..agents.campaign.mcp_invoke import build_mcp_invoke, project_paths_for
from ..agents.inference import (
    MissingInferenceCredential,
    MissingInferenceModel,
    require_inference_credential,
)
from ..config import settings
from ..db.schemas import (
    ChatSessionTable,
    SlackConfigTable,
    SlackProjectAskTable,
    SlackDetailTable,
    SlackLinkTable,
)
from ..services import chat_run as chat_run_module
from ..services import inbox, local_settings
from ..services import project as project_service
from . import access, audit, confirm, credentials, format, gate, limits, linking, store
from .confirm import Confirmations
from .events import Action, Event, HomeOpened, Message, keyword
from .home import Home, host_name
from .relay import Place, RunRelay
from .threads import (
    ChooseProject,
    NoProject,
    NotYourThread,
    conversation_for,
    held_ask,
    thread_of,
)
from .transport import SlackCallError, SlackTransport

log = logging.getLogger("vista.slack")

TransportFactory = Callable[[str, str], SlackTransport]
""" (bot token, app token) → a transport, not yet connected. """

WAITING = "hourglass_flowing_sand"
""" The reaction on a message that waits behind a running turn. """
TAKEN = "white_check_mark"
""" What replaces it once a turn has taken the message. """
NOT_STARTED = "Sorry, I couldn't start on that. Try again, or check the desktop app."
WHICH_PROJECT = "Which project should this go in?"
GONE = "That's no longer available."

Held = tuple[str, str]
""" A message waiting to go into a conversation's inbox: (text, its `ts`). """


following: dict[str, asyncio.Task] = {}
"""
Run id → the task showing that run in its thread. Kept across a service's
stop, so a turn still going when the settings are saved or VISTA quits ends
in its thread ("stopped", "VISTA quit before…"), and is not followed twice.
"""


_held: list[chat_run_module.ChatRun] = []
""" Slack runs that started while no service was connected to follow them. """
_holding = False
_unhold: Callable[[], None] | None = None


def _hold(run: chat_run_module.ChatRun) -> None:
    if _holding and run.channel == "slack":
        _held.append(run)


def watch_runs() -> None:
    """
    Before startup can start a turn: hold each Slack run until the service
    connects and follows it. Slack starts last, after a turn begun from a
    waiting message may already have answered.
    """
    global _holding, _unhold
    _holding = True
    if _unhold is None:
        _unhold = chat_run_module.on_run_started(_hold)


def _stop_holding() -> None:
    global _holding
    _holding = False
    _held.clear()


def socket_mode(bot_token: str, app_token: str) -> SlackTransport:
    from .socket_mode import SocketModeTransport

    return SocketModeTransport(bot_token=bot_token, app_token=app_token)


default_transport: TransportFactory = socket_mode
""" What a service built without a factory uses; tests replace it. """


PREVIEW_FIELDS = {
    "cluster": "System",
    "account": "Account",
    "nodes": "Nodes",
    "duration": "Walltime",
    "script": "Script",
}


async def preview_job(
    chat_session_id: uuid.UUID,
    tool_name: str,
    args: dict[str, Any],
) -> dict[str, str]:
    """A submit's card fields from `preview_hpc_job`, which contacts no cluster."""
    if tool_name.split("__")[-1] != "submit_hpc_job":
        return {}
    async with chat_run_module.session_factory() as db:
        chat = await db.get(ChatSessionTable, chat_session_id)
        if chat is None:
            return {}
        local = await local_settings.get_public(db)
        project_id = chat.project_id
    invoke = build_mcp_invoke(local, project_paths_for(project_id))
    text = await invoke("preview_hpc_job", args)
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() in PREVIEW_FIELDS:
            fields[PREVIEW_FIELDS[key.strip()]] = value.strip()
    return fields


async def _config() -> SlackConfigTable:
    async with chat_run_module.session_factory() as db:
        row = await store.get_config(db)
        snapshot = SlackConfigTable.model_validate(row.model_dump())
        await db.commit()
    return snapshot


class SlackService:
    def __init__(
        self,
        *,
        transport_factory: TransportFactory | None = None,
        host: str | None = None,
    ) -> None:
        self._factory = transport_factory  # None: `default_transport`, at start.
        self._host = host or host_name()
        self.slack: SlackTransport | None = None
        self.state = "off"
        """ off · connected · error. """
        self.detail: str | None = None
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._unhook: Callable[[], None] | None = None
        self._unlinked = access.UnlinkedReplies()
        self._confirmations: Confirmations | None = None
        self._home: Home | None = None

    # -- lifecycle -----------------------------------------------------------

    @staticmethod
    async def why_not(config: SlackConfigTable) -> str | None:
        """Why Slack should not run, or None when it should."""
        if not config.enabled:
            return "Slack is off."
        return store.why_not_enabled()

    async def start(self) -> bool:
        """Start if Slack is on. True when it started."""
        config = await _config()
        why = await self.why_not(config)
        if why is not None:
            self.state, self.detail = "off", why
            return False
        bot, app = store.bot_token(), store.app_token()
        assert bot and app  # `why_not` checked both.
        slack = self.slack = (self._factory or default_transport)(bot, app)
        gate.set_refusal(limits.refusal)
        confirm.set_preview(preview_job)
        self._stop = asyncio.Event()
        self._confirmations = Confirmations(slack)
        self._home = Home(slack, host=self._host)
        try:
            await self._confirmations.close_stale()
        except Exception:
            log.exception("Could not close Slack cards left open by the last run")
        await self._connect()
        self._unhook = chat_run_module.on_run_started(self._on_run)
        # Turns already going, and those that started (and may have ended)
        # before Slack connected, such as one startup began from a waiting
        # message: following a finished run replays it whole.
        for run in [*_held, *chat_run_module.chat_run_registry.active()]:
            self._on_run(run)
        _held.clear()
        self._spawn(self._reconnect_loop())
        self._spawn(self._every(settings.slack.reminder_interval, self._remind))
        self._spawn(self._every(settings.slack.home_refresh_interval, self._homes))
        return True

    async def stop(self) -> None:
        """Mark the Home tabs offline and disconnect. Bounded for a quick quit."""
        if self.slack is None:
            return
        slack, self.slack = self.slack, None
        self._stop.set()
        if self._unhook is not None:
            self._unhook()
            self._unhook = None
        if slack.connected and self._home is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    self._home.publish_all(online=False), timeout=2.0
                )
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []
        if self._confirmations is not None:
            # Before disconnecting, so the cards can still be redrawn.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._confirmations.stop(), timeout=5.0)
        with contextlib.suppress(Exception):
            await slack.disconnect()
        gate.set_refusal(gate.no_refusal)
        confirm.set_preview(confirm.no_preview)
        self.state, self.detail = "off", None

    async def _connect(self) -> None:
        assert self.slack is not None
        # The socket opens with the app-level token alone; a bot token Slack
        # rejects would leave it "connected" with every event dropped.
        try:
            await self.slack.identity()
        except SlackCallError as exc:
            log.warning("Slack rejected the bot token: %s", exc.error)
            self.state, self.detail = (
                "error",
                f"Slack rejected the bot token ({exc.error}). Paste a fresh one from the "
                "app's OAuth & Permissions page.",
            )
            return
        except Exception as exc:
            log.warning("Could not reach Slack: %s", exc)
            self.state, self.detail = "error", f"Could not reach Slack: {exc}"
            return
        try:
            await self.slack.connect(self.handle)
        except Exception as exc:
            log.warning("Could not connect to Slack: %s", exc)
            self.state, self.detail = "error", f"Could not reach Slack: {exc}"
            return
        self.state, self.detail = "connected", None
        async with chat_run_module.session_factory() as db:
            config = await store.get_config(db)
            config.online_since = store.now_iso()
            db.add(config)
            await db.commit()
        await self._homes()

    async def _reconnect_loop(self) -> None:
        """Connect again whenever the connection is down, as long as Slack is on."""
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=settings.slack.reconnect_interval
                )
            if self._stop.is_set() or self.slack is None:
                return
            if not self.slack.connected:
                await self._connect()

    async def _every(
        self, interval: float, job: Callable[[], Coroutine[Any, Any, Any]]
    ) -> None:
        while not self._stop.is_set():
            if self.slack is not None and self.slack.connected:
                try:
                    await job()
                except Exception:
                    log.exception("Slack background job failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=interval)

    async def _remind(self) -> None:
        assert self.slack is not None
        await credentials.CredentialReminder(self.slack).run_once()

    async def _homes(self) -> None:
        if self._home is not None:
            await self._home.publish_all()

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        self._tasks.append(asyncio.create_task(coro))

    # -- events --------------------------------------------------------------

    async def handle(self, event: Event) -> None:
        async with chat_run_module.session_factory() as db:
            config = await store.get_config(db)
            decision = await access.decide(db, event, config, self._unlinked)
            linked = decision.linked
            link = (
                SlackLinkTable.model_validate(linked.link.model_dump())
                if linked
                else None
            )
            await db.commit()
        if decision.verdict == "drop":
            return
        if decision.verdict == "reply":
            await self._reply(event, decision.reply or "", ephemeral=decision.ephemeral)
            return
        if decision.verdict == "link" and isinstance(event, Message):
            await self._link(event)
            return
        assert link is not None
        if isinstance(event, HomeOpened):
            if self._home is not None:
                await self._home.publish(event.team_id, event.user_id)
        elif isinstance(event, Action):
            await self._action(event, link)
        elif isinstance(event, Message):
            await self._message(event, link)

    async def _reply(self, event: Event, text: str, *, ephemeral: bool) -> None:
        assert self.slack is not None
        if isinstance(event, HomeOpened):
            return
        thread = event.root_ts if isinstance(event, Message) else event.thread_ts
        try:
            if ephemeral:
                await self.slack.ephemeral(
                    event.channel_id, event.user_id, text, thread_ts=thread
                )
            else:
                await self.slack.post(event.channel_id, text, thread_ts=thread)
        except SlackCallError as exc:
            log.info("Could not reply in Slack: %s", exc.error)

    async def _link(self, message: Message) -> None:
        async with chat_run_module.session_factory() as db:
            code = await linking.issue_code(
                db,
                team_id=message.team_id,
                slack_user_id=message.user_id,
                dm_channel_id=message.channel_id,
            )
        await self._reply(message, linking.code_message(code), ephemeral=False)

    async def _message(
        self,
        message: Message,
        link: SlackLinkTable,
    ) -> None:
        word = keyword(message)
        if word == "lock":
            await limits.lock(message.user_id)
            await audit.record(
                "locked",
                team_id=message.team_id,
                slack_user_id=message.user_id,
                collab_sub=link.collab_sub,
            )
            await self._reply(message, limits.LOCKED_REPLY, ephemeral=False)
            if self._home is not None:
                await self._home.publish(message.team_id, message.user_id)
            return
        if word == "unlock":
            await self._reply(message, limits.UNLOCK_REPLY, ephemeral=False)
            return
        try:
            async with chat_run_module.session_factory() as db:
                ask = await held_ask(
                    db, message.team_id, message.channel_id, message.root_ts
                )
                if ask is not None:
                    # A reply before the project is picked waits with the first.
                    if ask.slack_user_id != message.user_id:
                        raise NotYourThread(
                            "This thread is someone else's conversation with VISTA. "
                            "Mention me in a new message to start your own."
                        )
                    ask.prompts = [
                        *ask.prompts,
                        {"text": message.prompt, "ts": message.ts},
                    ]
                    db.add(ask)
                    await db.commit()
                    chat_id = None
                else:
                    conversation = await conversation_for(
                        db, message, project_id=link.project_id
                    )
                    chat_id = conversation.chat_session_id
                    chat = await db.get(ChatSessionTable, chat_id)
                    model = chat.inference_model if chat else None
                    await db.commit()
                    await self._check_model(model)
        except (NotYourThread, NoProject) as exc:
            await self._reply(message, str(exc), ephemeral=False)
            return
        except ChooseProject as exc:
            await self._ask_project(message, exc.choices)
            return
        except (MissingInferenceCredential, MissingInferenceModel) as exc:
            await self._reply(message, format.plain(exc.detail).text, ephemeral=False)
            return
        if chat_id is None:
            await self._mark(message.channel_id, message.ts, add=WAITING)
            return
        await self._send(
            chat_id,
            channel=message.channel_id,
            thread_ts=message.root_ts,
            team_id=message.team_id,
            slack_user_id=message.user_id,
            collab_sub=link.collab_sub,
            held=[(message.prompt, message.ts)],
        )

    @staticmethod
    async def _check_model(model: str | None) -> None:
        """Raise `MissingInference*` unless there is a model to send to, as the app checks."""
        async with chat_run_module.session_factory() as db:
            local = await local_settings.get_public(db)
            await db.commit()
        require_inference_credential(local, model)

    async def _send(
        self,
        chat_id: uuid.UUID,
        *,
        channel: str,
        thread_ts: str,
        team_id: str,
        slack_user_id: str,
        collab_sub: str | None,
        held: list[Held],
    ) -> None:
        """Record each message as a request, then put them in the inbox and wake it."""
        for text, _ in held:
            await audit.request(
                team_id=team_id,
                slack_user_id=slack_user_id,
                collab_sub=collab_sub,
                chat_session_id=chat_id,
                channel_id=channel,
                thread_ts=thread_ts,
                text=text,
            )
        await self._enqueue(chat_id, channel=channel, thread_ts=thread_ts, held=held)

    async def _enqueue(
        self, chat_id: uuid.UUID, *, channel: str, thread_ts: str, held: list[Held]
    ) -> None:
        """
        Put the messages in the conversation's inbox, in order, and wake it, as
        the app's send does. Marked ⏳ first if a turn is running, which will
        take them.
        """
        assert self.slack is not None
        registry = chat_run_module.chat_run_registry
        if registry.get(chat_id) is not None:
            for _, ts in held:
                await self._mark(channel, ts, add=WAITING)
        items = []
        async with chat_run_module.session_factory() as db:
            for text, ts in held:
                items.append(
                    await inbox.put(
                        db,
                        chat_id,
                        kind="message",
                        text=text,
                        payload={"slack": {"channel": channel, "ts": ts}},
                        notify=False,
                    )
                )
        try:
            run = await registry.wake(chat_id)
        except Exception:
            log.exception("Could not start a turn for a Slack message")
            async with chat_run_module.session_factory() as db:
                for item in items:
                    with contextlib.suppress(inbox.ItemTaken):
                        assert item.id is not None
                        await inbox.withdraw(db, chat_id, item.id)
            await self._post(channel, NOT_STARTED, thread_ts=thread_ts)
            return
        started = {i.id for i in run.items} if run is not None else set()
        if any(item.id not in started for item in items):
            # The running turn takes them at its next step; show the desktop too.
            await registry.announce_inbox(chat_id)

    async def _post(self, channel: str, text: str, *, thread_ts: str) -> None:
        assert self.slack is not None
        try:
            await self.slack.post(channel, text, thread_ts=thread_ts)
        except SlackCallError as exc:
            log.info("Could not post in Slack: %s", exc.error)

    # -- which project -------------------------------------------------------

    async def _ask_project(
        self, message: Message, choices: list[tuple[uuid.UUID, str]]
    ) -> None:
        """Hold the thread's first message and ask which project it goes in."""
        assert self.slack is not None
        try:
            await self._check_model(None)
        except (MissingInferenceCredential, MissingInferenceModel) as exc:
            await self._reply(message, format.plain(exc.detail).text, ephemeral=False)
            return
        async with chat_run_module.session_factory() as db:
            ask = SlackProjectAskTable(
                team_id=message.team_id,
                channel_id=message.channel_id,
                thread_ts=message.root_ts,
                slack_user_id=message.user_id,
                prompts=[{"text": message.prompt, "ts": message.ts}],
                created_at=store.now_iso(),
            )
            ask_id = ask.id  # Before the commit expires it.
            db.add(ask)
            await db.commit()
        reply = format.project_picker(
            WHICH_PROJECT, str(ask_id), [(str(pid), name) for pid, name in choices]
        )
        try:
            ts = await self.slack.post(
                message.channel_id,
                reply.text,
                blocks=reply.blocks,
                thread_ts=message.root_ts,
            )
        except SlackCallError as exc:
            log.warning("Could not ask which project: %s", exc.error)
            return
        async with chat_run_module.session_factory() as db:
            row = await db.get(SlackProjectAskTable, ask_id)
            if row is not None:
                row.message_ts = ts
                db.add(row)
            await db.commit()

    async def _pick(self, action: Action, link: SlackLinkTable) -> None:
        """A project was tapped: start the thread's conversation there with what waited."""
        assert self.slack is not None
        ask_id, _, project_id = action.value.partition(":")
        try:
            ask_key, project_key = uuid.UUID(ask_id), uuid.UUID(project_id)
        except ValueError:
            await self._reply(action, GONE, ephemeral=True)
            return
        async with chat_run_module.session_factory() as db:
            ask = await db.get(SlackProjectAskTable, ask_key)
            if ask is None:
                await db.commit()
                await self._reply(action, GONE, ephemeral=True)
                return
            if ask.slack_user_id != action.user_id:
                owner = ask.slack_user_id
                await db.commit()
                await self._reply(
                    action,
                    f"Only <@{owner}> can choose for this thread.",
                    ephemeral=True,
                )
                return
            projects = await project_service.list_projects(db)
            project = next((p for p in projects if p.id == project_key), None)
            if project is None:
                await db.commit()
                await self._reply(
                    action, "That project is gone. Pick another.", ephemeral=True
                )
                return
            name = project.name
            held = [(p["text"], p["ts"]) for p in ask.prompts]
            where = (ask.team_id, ask.channel_id, ask.thread_ts, ask.message_ts)
            await db.commit()
        team_id, channel, thread_ts, picker_ts = where
        try:
            await self._check_model(None)
        except (MissingInferenceCredential, MissingInferenceModel) as exc:
            # The question stays, to be answered once a model is set up.
            await self._reply(action, format.plain(exc.detail).text, ephemeral=True)
            return
        first = Message(
            team_id=team_id,
            channel_id=channel,
            user_id=action.user_id,
            text=held[0][0] if held else "",
            ts=thread_ts,
        )
        async with chat_run_module.session_factory() as db:
            ask = await db.get(SlackProjectAskTable, ask_key)
            if ask is None:  # A second tap got here first.
                await db.commit()
                return
            await db.delete(ask)
            conversation = await conversation_for(db, first, project_id=project_key)
            chat_id = conversation.chat_session_id
        if picker_ts is not None:
            with contextlib.suppress(SlackCallError):
                await self.slack.update(
                    channel, picker_ts, f"Going in *{format.to_mrkdwn(name)}*."
                )
        await self._send(
            chat_id,
            channel=channel,
            thread_ts=thread_ts,
            team_id=team_id,
            slack_user_id=action.user_id,
            collab_sub=link.collab_sub,
            held=held,
        )

    async def _mark(
        self, channel: str, ts: str, *, add: str, remove: str | None = None
    ) -> bool:
        """Swap the bot's reaction on a message. False when `remove` was not there."""
        assert self.slack is not None
        if remove is not None:
            try:
                await self.slack.unreact(channel, ts, remove)
            except SlackCallError as exc:
                if exc.error != "no_reaction":
                    log.info("Could not remove a Slack reaction: %s", exc.error)
                return False
        try:
            await self.slack.react(channel, ts, add)
        except SlackCallError as exc:
            if exc.error != "already_reacted":
                log.info("Could not add a Slack reaction: %s", exc.error)
        return True

    async def _taken(self, item: dict[str, Any]) -> None:
        """A turn took an inbox item: a Slack message that waited gets ✅ for its ⏳."""
        where = (item.get("payload") or {}).get("slack") or {}
        if item.get("kind") == "message" and where.get("channel") and where.get("ts"):
            await self._mark(where["channel"], where["ts"], add=TAKEN, remove=WAITING)

    async def _withdrawn(self, chat_id: uuid.UUID, item_ids: list[int]) -> None:
        """A Stop withdrew these waiting messages: take their ⏳ off, as none will be answered."""
        for item_id in item_ids:
            async with chat_run_module.session_factory() as db:
                try:
                    item = await inbox.get(db, chat_id, item_id)
                except inbox.ItemNotFound:
                    continue
                where = (item.payload or {}).get("slack") or {}
                await db.commit()
            if where.get("channel") and where.get("ts"):
                assert self.slack is not None
                with contextlib.suppress(SlackCallError):
                    await self.slack.unreact(where["channel"], where["ts"], WAITING)

    # -- following runs ------------------------------------------------------

    def _on_run(self, run: chat_run_module.ChatRun) -> None:
        """A run started: show it in its thread if it is a Slack conversation's."""
        if run.channel != "slack" or self.slack is None or run.run_id in following:
            return
        task = asyncio.create_task(self._follow(run))
        following[run.run_id] = task
        task.add_done_callback(lambda _: following.pop(run.run_id, None))

    async def _follow(self, run: chat_run_module.ChatRun) -> None:
        """Post the run's progress and answer in its thread; offer its prompts there."""
        try:
            await self._show(run)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Could not show run %s in Slack", run.run_id)

    async def _show(self, run: chat_run_module.ChatRun) -> None:
        assert self.slack is not None and self._confirmations is not None
        async with chat_run_module.session_factory() as db:
            thread = await thread_of(db, run.chat_session_id)
            if thread is None:
                log.warning("No Slack thread for conversation %s", run.chat_session_id)
                return
            place = Place(
                channel=thread.channel_id,
                thread_ts=thread.thread_ts,
                slack_user_id=thread.slack_user_id,
            )
            team_id = thread.team_id
        confirmations = self._confirmations

        async def on_prompt(event: chat_run_module.RunEvent) -> None:
            await confirmations.offer(
                event,
                place=place,
                team_id=team_id,
                chat_session_id=run.chat_session_id,
                mention=run.origin == "task",
            )

        relay = RunRelay(
            self.slack,
            place,
            on_prompt=on_prompt,
            on_taken=self._taken,
            on_withdrawn=lambda ids: self._withdrawn(run.chat_session_id, ids),
        )
        await relay.follow(run)

    async def _action(self, action: Action, link: SlackLinkTable) -> None:
        assert self.slack is not None and self._confirmations is not None
        if action.action_id.startswith(format.PICK_PROJECT):
            await self._pick(action, link)
            return
        if action.action_id != format.SHOW_MORE:
            await self._confirmations.on_action(action)
            return
        await self._show_more(action)

    async def _show_more(self, action: Action) -> None:
        """
        Expand the answer in place: the message becomes the whole answer, or as
        much as one message holds, without the button, and only what is left is
        posted below it. Once: the stored answer is used up by the tap.
        """
        assert self.slack is not None
        async with chat_run_module.session_factory() as db:
            row = await db.get(SlackDetailTable, action.value)
            if row is None:  # Already expanded (or expired): nothing to add.
                await db.commit()
                return
            if row.slack_user_id != action.user_id:
                await db.commit()
                await self._reply(action, "That's no longer available.", ephemeral=True)
                return
            full = row.text
            await db.delete(row)
            await db.commit()
        first, rest = format.expanded(full)
        thread = action.thread_ts or action.message_ts
        try:
            await self.slack.update(
                action.channel_id, action.message_ts, first.text, blocks=first.blocks
            )
        except SlackCallError as exc:
            log.info("Could not expand the answer in place: %s", exc.error)
            rest = [first, *rest]
        for reply in rest:
            await self.slack.post(
                action.channel_id,
                reply.text,
                blocks=reply.blocks or None,
                thread_ts=thread,
            )


# The one service of this process: started by the lifespan, restarted when the
# settings change, stopped on quit.
service: SlackService | None = None


async def start(**kwargs: Any) -> SlackService | None:
    """At start-up. None, with nothing started, when Slack is off here."""
    global service
    candidate = SlackService(**kwargs)
    service = candidate
    try:
        return candidate if await candidate.start() else None
    finally:
        _stop_holding()  # Followed now, or Slack is off and nothing will be.


async def stop() -> None:
    if service is not None:
        await service.stop()


async def restart() -> None:
    """After the settings change: stop, and start again if still on."""
    global service
    kwargs: dict[str, Any] = {}
    if service is not None:
        kwargs = {"transport_factory": service._factory, "host": service._host}
        watch_runs()  # A turn that starts in between is followed after.
        await service.stop()
    await start(**kwargs)


async def refresh_homes() -> None:
    """Republish the App Home tabs now, as after the lock changes on the desktop."""
    if service is None or service.slack is None or not service.slack.connected:
        return
    try:
        await service._homes()
    except Exception:
        log.exception("Could not refresh the Slack Home tabs")


def status() -> tuple[str, str | None]:
    """What the settings page shows about this machine's connection."""
    if service is None:
        return "off", None
    return service.state, service.detail
