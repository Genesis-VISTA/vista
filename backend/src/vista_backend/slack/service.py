"""
The Slack service: everything in `slack/` put together and run with the backend.

`SlackService.start` does nothing unless Slack is on, this machine is set to
handle it, and both tokens are in the secret store. Then, in order: install the
gate's refusal and the card's preview, expire cards left open by the last run,
take the lease (or stand by while another machine holds it), connect, and run
the background loops — lease heartbeat, job notices, credential reminders and
the App Home. `stop` tells Slack VISTA is going offline, then undoes all of it.

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
from ..agents.inference import MissingInferenceCredential, MissingInferenceModel
from ..config import settings
from ..db.schemas import (
    ChatSessionTable,
    ProjectPublic,
    SlackConfigTable,
    SlackDetailTable,
    UserPublicWithConfig,
    UserTable,
)
from ..services import chat_run as chat_run_module
from ..services.chat_run import RunBusy
from ..services.conversation import start_turn
from . import access, audit, confirm, credentials, format, gate, limits, linking, store
from .confirm import Confirmations
from .events import Action, Event, HomeOpened, Message, keyword
from .home import Home
from .jobs import JobMonitor
from .lease import Lease, host_name
from .notices import Notices
from .power import SleepPrevention, SleepWatcher
from .relay import Place, RunRelay
from .threads import NoProject, NotYourThread, ThreadQueue, conversation_for
from .transport import SlackCallError, SlackTransport

log = logging.getLogger("vista.slack")

TransportFactory = Callable[[str, str], SlackTransport]
""" (bot token, app token) → a transport, not yet connected. """

QUEUE_FULL = (
    "I'm still working on this thread and a few messages are already waiting. "
    "Send this again when I've answered."
)
BUSY = "This conversation already has a turn running in the desktop app. Try again when it's done."


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
    user_id: uuid.UUID,
    chat_session_id: uuid.UUID,
    tool_name: str,
    args: dict[str, Any],
) -> dict[str, str]:
    """A submit's card fields from `preview_hpc_job`, which contacts no cluster."""
    if tool_name.split("__")[-1] != "submit_hpc_job":
        return {}
    async with chat_run_module.session_factory() as db:
        row = await db.get(UserTable, user_id)
        chat = await db.get(ChatSessionTable, chat_session_id)
        if row is None or chat is None:
            return {}
        user = UserPublicWithConfig.model_validate(row)
        project_id = chat.project_id
    invoke = build_mcp_invoke(user, project_paths_for(project_id, user_id))
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
        watch_power: bool = True,
    ) -> None:
        self._factory = transport_factory  # None: `default_transport`, at start.
        self._host = host or host_name()
        self._watch_power = watch_power
        self.slack: SlackTransport | None = None
        self.state = "off"
        """ off · connected · standby (another machine has Slack) · error. """
        self.detail: str | None = None
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._queue = ThreadQueue()
        self._unlinked = access.UnlinkedReplies()
        self._confirmations: Confirmations | None = None
        self._lease: Lease | None = None
        self._notices: Notices | None = None
        self._home: Home | None = None
        self._sleep: SleepWatcher | None = None
        self._awake: SleepPrevention | None = None

    # -- lifecycle -----------------------------------------------------------

    @staticmethod
    async def why_not(config: SlackConfigTable) -> str | None:
        """Why this machine should not run Slack, or None when it should."""
        if not config.enabled:
            return "Slack is off."
        if not config.handle_here:
            return "This machine is not set to handle Slack."
        return store.why_not_enabled()

    async def start(self) -> bool:
        """Start if Slack is on for this machine. True when it started."""
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
        self._lease = Lease(slack, instance_id=config.instance_id, host=self._host)
        self._notices = Notices(slack, host=self._host)
        self._home = Home(slack, host=self._host)
        try:
            await self._confirmations.close_stale()
        except Exception:
            log.exception("Could not close Slack cards left open by the last run")
        await self._connect_if_ours()
        self._spawn(self._lease_loop())
        self._spawn(
            JobMonitor(slack).run_forever(
                interval=settings.slack.job_poll_interval, stop_event=self._stop
            )
        )
        self._spawn(self._every(settings.slack.reminder_interval, self._remind))
        self._spawn(self._every(settings.slack.home_refresh_interval, self._homes))
        if self._watch_power:
            self._sleep = SleepWatcher(self._before_sleep)
            await self._sleep.start()
            if config.prevent_sleep:
                self._awake = SleepPrevention()
                await self._awake.start()
        return True

    async def stop(self) -> None:
        """Tell Slack, let go of the lease, disconnect. Bounded for a quick quit."""
        if self.slack is None:
            return
        slack, self.slack = self.slack, None
        self._stop.set()
        if slack.connected and self._notices is not None:
            await self._notices.going_offline()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    self._home.publish_all(online=False),  # type: ignore[union-attr]
                    timeout=2.0,
                )
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._lease.release(), timeout=2.0)  # type: ignore[union-attr]
        if self._notices is not None:
            self._notices.cancel()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []
        if self._confirmations is not None:
            self._confirmations.stop()
        for power in (self._sleep, self._awake):
            if power is not None:
                with contextlib.suppress(Exception):
                    await power.stop()
        self._sleep = self._awake = None
        with contextlib.suppress(Exception):
            await slack.disconnect()
        gate.set_refusal(gate.no_refusal)
        confirm.set_preview(confirm.no_preview)
        self.state, self.detail = "off", None

    async def take_over(self) -> None:
        """From the desktop: handle Slack here even though another machine is."""
        if self._lease is None or self.slack is None:
            return
        await self._lease.take_over()
        await self._connect()

    async def _connect_if_ours(self) -> None:
        assert self._lease is not None and self.slack is not None
        try:
            decision = await self._lease.acquire()
        except Exception as exc:
            log.warning("Could not check who handles Slack: %s", exc)
            self.state, self.detail = "error", f"Could not reach Slack: {exc}"
            return
        if decision.ours:
            await self._connect()
        else:
            holder = decision.holder
            self.state = "standby"
            self.detail = (
                f"Slack is handled by {holder.host if holder else 'another machine'}."
            )
            log.info("Not connecting to Slack: %s", self.detail)

    async def _connect(self) -> None:
        assert self.slack is not None and self._notices is not None
        if self.slack.connected:
            return
        await self.slack.connect(self.handle)
        self.state, self.detail = "connected", None
        await self._notices.connected()
        await self._homes()

    async def _lease_loop(self) -> None:
        assert self._lease is not None
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=settings.slack.lease_heartbeat
                )
            if self._stop.is_set() or self.slack is None:
                return
            try:
                if not self.slack.connected:
                    await self._connect_if_ours()
                elif not await self._lease.heartbeat():
                    # Taken over: the other machine says what it needs to.
                    await self.slack.disconnect()
                    if self._notices is not None:
                        self._notices.cancel()
                    holder = (await self._lease.check()).holder
                    self.state = "standby"
                    self.detail = f"Slack was taken over by {holder.host if holder else 'another machine'}."
            except Exception:
                log.exception("Slack lease heartbeat failed")

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

    async def _before_sleep(self) -> None:
        if self.slack is not None and self.slack.connected and self._notices:
            await self._notices.going_offline()

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        self._tasks.append(asyncio.create_task(coro))

    # -- events --------------------------------------------------------------

    async def handle(self, event: Event) -> None:
        async with chat_run_module.session_factory() as db:
            config = await store.get_config(db)
            decision = await access.decide(db, event, config, self._unlinked)
            linked = decision.linked
            user = UserPublicWithConfig.model_validate(linked.user) if linked else None
            project_id = linked.link.project_id if linked else None
            await db.commit()
        if decision.verdict == "drop":
            return
        if decision.verdict == "reply":
            await self._reply(event, decision.reply or "", ephemeral=decision.ephemeral)
            return
        if decision.verdict == "link" and isinstance(event, Message):
            await self._link(event)
            return
        assert user is not None
        if isinstance(event, HomeOpened):
            if self._home is not None:
                await self._home.publish(event.team_id, event.user_id)
        elif isinstance(event, Action):
            await self._action(event)
        elif isinstance(event, Message):
            await self._message(event, user, project_id)

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
        user: UserPublicWithConfig,
        project_id: uuid.UUID | None,
    ) -> None:
        word = keyword(message)
        if word == "lock":
            await limits.lock(message.user_id)
            await audit.record(
                "locked",
                team_id=message.team_id,
                slack_user_id=message.user_id,
                user_id=user.id,
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
                conversation = await conversation_for(
                    db, message, user=user, project_id=project_id
                )
                chat_id = conversation.chat_session_id
                project = ProjectPublic.model_validate(conversation.project)
                await db.commit()
        except (NotYourThread, NoProject) as exc:
            await self._reply(message, str(exc), ephemeral=False)
            return
        queued = await self._queue.run(
            chat_id, lambda: self._turn(message, user, project, chat_id)
        )
        if not queued:
            await self._reply(message, QUEUE_FULL, ephemeral=False)

    async def _turn(
        self,
        message: Message,
        user: UserPublicWithConfig,
        project: ProjectPublic,
        chat_id: uuid.UUID,
    ) -> None:
        assert self.slack is not None and self._confirmations is not None
        place = Place(
            channel=message.channel_id,
            thread_ts=message.root_ts,
            slack_user_id=message.user_id,
        )
        confirmations = self._confirmations

        async def on_prompt(event: chat_run_module.RunEvent) -> None:
            await confirmations.offer(
                event,
                place=place,
                team_id=message.team_id,
                chat_session_id=chat_id,
                user_id=user.id,
            )

        relay = RunRelay(self.slack, place, on_prompt=on_prompt)
        await relay.start()
        await audit.request(
            team_id=message.team_id,
            slack_user_id=message.user_id,
            user_id=user.id,
            chat_session_id=chat_id,
            channel_id=message.channel_id,
            thread_ts=message.root_ts,
            text=message.prompt,
        )
        try:
            async with chat_run_module.session_factory() as db:
                run = await start_turn(
                    db,
                    user=user,
                    project=project,
                    chat_session_id=chat_id,
                    user_prompt=message.prompt,
                    channel="slack",
                )
        except (MissingInferenceCredential, MissingInferenceModel) as exc:
            await self._say_instead(relay, place, exc.detail)
            return
        except RunBusy:
            await self._say_instead(relay, place, BUSY)
            return
        await relay.follow(run)

    async def _say_instead(self, relay: RunRelay, place: Place, text: str) -> None:
        """Replace the progress message when the turn never started."""
        assert self.slack is not None and relay.progress_ts is not None
        reply = format.plain(text)
        try:
            await self.slack.update(place.channel, relay.progress_ts, reply.text)
        except SlackCallError:
            await self.slack.post(place.channel, reply.text, thread_ts=place.thread_ts)

    async def _action(self, action: Action) -> None:
        assert self.slack is not None and self._confirmations is not None
        if action.action_id != format.SHOW_MORE:
            await self._confirmations.on_action(action)
            return
        async with chat_run_module.session_factory() as db:
            row = await db.get(SlackDetailTable, action.value)
            text = row.text if row and row.slack_user_id == action.user_id else None
            await db.commit()
        if text is None:
            await self._reply(action, "That's no longer available.", ephemeral=True)
            return
        for reply in format.detail_messages(text):
            await self.slack.post(
                action.channel_id,
                reply.text,
                blocks=reply.blocks or None,
                thread_ts=action.thread_ts or action.message_ts,
            )


# The one service of this process: started by the lifespan, restarted when the
# settings change, stopped on quit.
service: SlackService | None = None


async def start(**kwargs: Any) -> SlackService | None:
    """At start-up. None, with nothing started, when Slack is off here."""
    global service
    candidate = SlackService(**kwargs)
    service = candidate
    return candidate if await candidate.start() else None


async def stop() -> None:
    if service is not None:
        await service.stop()


async def restart() -> None:
    """After the settings change: stop, and start again if still on."""
    global service
    kwargs: dict[str, Any] = {}
    if service is not None:
        kwargs = {
            "transport_factory": service._factory,
            "host": service._host,
            "watch_power": service._watch_power,
        }
        await service.stop()
    await start(**kwargs)


def status() -> tuple[str, str | None]:
    """What the settings page shows about this machine's connection."""
    if service is None:
        return "off", None
    return service.state, service.detail
