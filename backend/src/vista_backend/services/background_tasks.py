"""
Background commands: sandbox commands that outlive the turn that started them
(openspec change background-tasks, Decisions 4 and 5).

The agent calls the dev MCP server's `run_bash` with `background=true`. The
agent's `process_tool_call` hands that call here (`start_command`): the limits
are checked, a `background_task` record is made, and the call goes on to the
sandbox with the record's id and log path, which starts the command detached
and returns at once.

A watcher task then waits on the command through the sandbox's hidden
`wait_task` tool, holding the agent that started it (`TTLPool.hold`), so its
sandbox lives as long as the command even after a Settings or model change has
moved the conversation to a new agent. When the command ends the watcher
records how, reads the tail of its log from the host side of the volume, and
puts a `task_event` in the conversation's inbox. The time limit is the
watcher's: at the deadline it ends the command with `signal_task`. Stopping a
task goes through its watcher too, so the signal reaches the sandbox that owns
the process.

Records answer `list_tasks` and `stop_task` whichever agent the conversation
has now. The registry is in-process, like the chat run registry.
"""

import asyncio
import contextlib
import logging
import math
import time
import uuid
from datetime import datetime
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.db import get_engine
from ..db.schemas import BackgroundTaskTable
from ..utils.misc import now_iso
from . import inbox as inbox_service

log = logging.getLogger(__name__)

COMMAND_LIMIT = 4
""" Background commands one conversation may have running at once. """
DEFAULT_TIMEOUT_S = 60 * 60
MAX_TIMEOUT_S = 24 * 60 * 60
WAIT_SLICE_S = 25 * 60
""" The longest one `wait_task` call is held: under the dev server's 1860 s read timeout. """
TAIL_LINES = 20
TAIL_BYTES = 8 * 1024
SANDBOX_TASKS_DIR = "/mnt/data/tasks"

COMMAND_ENDED_STATES = frozenset({"exited", "stopped", "timed_out", "interrupted"})


def session_factory() -> AsyncSession:
    """The session records are read and written through; tests point it at their own engine."""
    return AsyncSession(get_engine())


class TaskRefused(Exception):
    """A start the limits do not allow. The message is for the agent."""


SandboxCall = Callable[[str, dict[str, Any]], Awaitable[Any]]
""" Calls one of the dev MCP server's hidden task tools and returns its structured result. """


@dataclass
class CommandStart:
    """What `start_command` hands back to the agent's tool call."""

    record: BackgroundTaskTable
    args: dict[str, Any]
    """ The `run_bash` arguments to send on to the sandbox. """


def sandbox_log_path(task_id: uuid.UUID) -> str:
    return f"{SANDBOX_TASKS_DIR}/{task_id}.log"


def host_log_path(volume_root: Path, record: BackgroundTaskTable) -> Path:
    """Where the sandbox's `/mnt/...` log is on the host: the project volume is `/mnt`."""
    sandbox = record.log_path or sandbox_log_path(record.id)
    return volume_root / sandbox.removeprefix("/mnt/")


def read_tail(path: Path, lines: int = TAIL_LINES) -> str:
    """The last `lines` lines of a log, read from its end only."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            data = f.read()
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


def format_runtime(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _parse_timeout(value: Any) -> int:
    if value is None:
        return DEFAULT_TIMEOUT_S
    try:
        timeout = int(value)
    except TypeError, ValueError:
        raise TaskRefused(f"timeout must be a number of seconds, not {value!r}.")
    if timeout <= 0:
        raise TaskRefused("timeout must be a positive number of seconds.")
    if timeout > MAX_TIMEOUT_S:
        raise TaskRefused(
            f"A background command may run at most 24 hours ({MAX_TIMEOUT_S} seconds); "
            f"{timeout} seconds was asked for."
        )
    return timeout


async def create_command(
    db: AsyncSession,
    *,
    chat_session_id: uuid.UUID,
    project_id: uuid.UUID,
    command: str,
    timeout: Any = None,
) -> BackgroundTaskTable:
    """Make the record for a background command, or refuse it (`TaskRefused`)."""
    timeout_s = _parse_timeout(timeout)
    running = (
        await db.exec(
            select(func.count())
            .select_from(BackgroundTaskTable)
            .where(
                BackgroundTaskTable.chat_session_id == chat_session_id,
                BackgroundTaskTable.kind == "command",
                BackgroundTaskTable.state == "running",
            )
        )
    ).one()
    if running >= COMMAND_LIMIT:
        raise TaskRefused(
            f"This conversation already has {COMMAND_LIMIT} background commands running, "
            f"the limit. Wait for one to end, or stop one with stop_task."
        )
    count = (
        await db.exec(
            select(func.count())
            .select_from(BackgroundTaskTable)
            .where(BackgroundTaskTable.chat_session_id == chat_session_id)
        )
    ).one()
    record = BackgroundTaskTable(
        chat_session_id=chat_session_id,
        project_id=project_id,
        label=f"bg-{count + 1}",
        kind="command",
        state="running",
        command=command,
        timeout_s=timeout_s,
        created_at=now_iso(),
    )
    record.log_path = sandbox_log_path(record.id)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return BackgroundTaskTable.model_validate(record)


async def start_command(
    *,
    chat_session_id: uuid.UUID | None,
    project_id: uuid.UUID,
    tool_args: dict[str, Any],
) -> CommandStart:
    """Check and record a background `run_bash`, and give the arguments to send on."""
    if chat_session_id is None:
        raise TaskRefused(
            "A background command needs a conversation to report back to."
        )
    command = tool_args.get("command")
    if not isinstance(command, str) or not command.strip():
        raise TaskRefused("A background command needs a command to run.")
    async with session_factory() as db:
        record = await create_command(
            db,
            chat_session_id=chat_session_id,
            project_id=project_id,
            command=command,
            timeout=tool_args.get("timeout"),
        )
    args = {
        **{k: v for k, v in tool_args.items() if k not in ("task_id", "log_path")},
        "background": True,
        "task_id": str(record.id),
        "log_path": record.log_path,
    }
    return CommandStart(record=record, args=args)


def started_message(record: BackgroundTaskTable) -> str:
    return (
        f"Started background task {record.label} (`{record.command}`). Its output goes to "
        f"{record.log_path}, which you can read while it runs. It is stopped after "
        f"{format_runtime(record.timeout_s or DEFAULT_TIMEOUT_S)}. You will be told when it "
        f"ends, in this turn or a new one; use list_tasks to see it and stop_task to end it."
    )


async def get(db: AsyncSession, task_id: uuid.UUID) -> BackgroundTaskTable | None:
    record = await db.get(BackgroundTaskTable, task_id, populate_existing=True)
    return BackgroundTaskTable.model_validate(record) if record else None


async def list_for_chat(
    db: AsyncSession, chat_session_id: uuid.UUID
) -> list[BackgroundTaskTable]:
    rows = (
        await db.exec(
            select(BackgroundTaskTable)
            .where(BackgroundTaskTable.chat_session_id == chat_session_id)
            .order_by(col(BackgroundTaskTable.created_at))
        )
    ).all()
    return [BackgroundTaskTable.model_validate(r) for r in rows]


async def find(
    db: AsyncSession, chat_session_id: uuid.UUID, ref: str
) -> BackgroundTaskTable | None:
    """A conversation's task by its label (`bg-3`) or id."""
    for record in await list_for_chat(db, chat_session_id):
        if ref in (record.label, str(record.id)):
            return record
    return None


async def record_end(
    task_id: uuid.UUID, state: str, exit_status: int | None = None
) -> BackgroundTaskTable | None:
    async with session_factory() as db:
        record = await db.get(BackgroundTaskTable, task_id)
        if record is None:  # the conversation was deleted
            return None
        record.state = state
        record.exit_status = exit_status
        record.ended_at = now_iso()
        db.add(record)
        await db.commit()
        await db.refresh(record)
        return BackgroundTaskTable.model_validate(record)


def runtime_of(record: BackgroundTaskTable, now: float | None = None) -> float:
    """Seconds between the record's start and its end, or now while it runs."""
    start = datetime.fromisoformat(record.created_at)
    end = (
        datetime.fromisoformat(record.ended_at)
        if record.ended_at
        else datetime.fromtimestamp(
            now if now is not None else time.time(), start.tzinfo
        )
    )
    return max(0.0, (end - start).total_seconds())


def how_it_ended(record: BackgroundTaskTable) -> str:
    if record.state == "exited":
        return f"exited with status {record.exit_status}"
    if record.state == "timed_out":
        return (
            f"was stopped at its time limit of {format_runtime(record.timeout_s or 0)}"
        )
    if record.state == "stopped":
        return "was stopped"
    return "was interrupted: its sandbox went away before it ended"


def end_event(record: BackgroundTaskTable, tail: str) -> tuple[str, dict[str, Any]]:
    """The `task_event` text the agent receives, and the payload the UI's event row shows."""
    runtime = format_runtime(runtime_of(record))
    ended = how_it_ended(record)
    lines = [
        f"Background task {record.label} {ended} after {runtime}.",
        f"Command: {record.command}",
        f"Log: {record.log_path}",
    ]
    lines += ["Last lines of output:", tail] if tail else ["It printed nothing."]
    summary_end = (
        f"exit {record.exit_status}"
        if record.state == "exited"
        else record.state.replace("_", " ")
    )
    payload = {
        "task_id": str(record.id),
        "label": record.label,
        "kind": "command",
        "state": record.state,
        "exit_status": record.exit_status,
        "runtime": runtime,
        "log_path": record.log_path,
        "summary": f"{record.label} finished · {summary_end} · {runtime}",
    }
    return "\n".join(lines), payload


def describe(record: BackgroundTaskTable) -> str:
    """One line of `list_tasks`."""
    runtime = format_runtime(runtime_of(record))
    if record.kind == "command":
        state = (
            f"exited {record.exit_status}" if record.state == "exited" else record.state
        )
        return (
            f"{record.label}  command  {state}  {runtime}  `{record.command}`  "
            f"log {record.log_path}"
        )
    return f"{record.label}  {record.kind}  {record.state}  {runtime}"


@dataclass
class _Watch:
    record: BackgroundTaskTable
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)


class BackgroundTaskRegistry:
    def __init__(self) -> None:
        self._watches: dict[uuid.UUID, _Watch] = {}

    def watching(self, task_id: uuid.UUID) -> bool:
        return task_id in self._watches

    async def watch_command(
        self,
        record: BackgroundTaskTable,
        *,
        call: SandboxCall,
        volume_root: Path,
        hold: AbstractAsyncContextManager[Any] | None = None,
    ) -> None:
        """
        Follow a started command until it ends, then report it to its conversation.

        `hold` is entered here, before returning, while the turn that started the
        command still holds its agent, so the agent cannot be cleaned up between.
        """
        stack = contextlib.AsyncExitStack()
        if hold is not None:
            await stack.enter_async_context(hold)
        watch = _Watch(record=record)
        self._watches[record.id] = watch
        watch.task = asyncio.create_task(
            self._watch_command(watch, call=call, volume_root=volume_root, stack=stack)
        )

    async def stop(self, task_id: uuid.UUID) -> BackgroundTaskTable | None:
        """
        End a task through its watcher and wait until it is recorded. A running
        record nothing watches (its watcher died with an earlier backend) is
        recorded as interrupted. Returns the record as it ends, or None if unknown.
        """
        watch = self._watches.get(task_id)
        if watch is not None:
            watch.stop.set()
            await watch.done.wait()
        async with session_factory() as db:
            record = await get(db, task_id)
        if record is not None and record.state == "running":
            record = await record_end(task_id, "interrupted")
        return record

    async def wait(self, task_id: uuid.UUID) -> None:
        """Until the task's watcher has recorded and reported its end (for tests)."""
        watch = self._watches.get(task_id)
        if watch is not None:
            await watch.done.wait()

    async def shutdown(self) -> None:
        """
        Stop watching, for a quit. Nothing is recorded: the commands end with
        their sandboxes, and the next start marks them interrupted.
        """
        watches = list(self._watches.values())
        for watch in watches:
            if watch.task is not None:
                watch.task.cancel()
        for watch in watches:
            if watch.task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await watch.task

    async def _watch_command(
        self,
        watch: _Watch,
        *,
        call: SandboxCall,
        volume_root: Path,
        stack: contextlib.AsyncExitStack,
    ) -> None:
        record = watch.record
        task_id = str(record.id)
        try:
            async with stack:
                state, exit_status = await self._follow(watch, call, task_id)
            ended = await record_end(record.id, state, exit_status)
            if ended is not None:
                text, payload = end_event(
                    ended, read_tail(host_log_path(volume_root, ended))
                )
                async with session_factory() as db:
                    await inbox_service.put(
                        db,
                        ended.chat_session_id,
                        kind="task_event",
                        text=text,
                        payload=payload,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Watching background task %s failed", record.label)
            with contextlib.suppress(Exception):
                await record_end(record.id, "interrupted")
        finally:
            self._watches.pop(record.id, None)
            watch.done.set()

    async def _follow(
        self, watch: _Watch, call: SandboxCall, task_id: str
    ) -> tuple[str, int | None]:
        """Wait on the command until it ends, is stopped, or reaches its limit."""
        deadline = time.monotonic() + (watch.record.timeout_s or DEFAULT_TIMEOUT_S)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return await self._end(call, task_id, "timed_out")
            waiting = asyncio.ensure_future(
                call(
                    "wait_task",
                    {
                        "id": task_id,
                        "max_wait": math.ceil(min(remaining, WAIT_SLICE_S)),
                    },
                )
            )
            stopping = asyncio.ensure_future(watch.stop.wait())
            try:
                await asyncio.wait(
                    {waiting, stopping}, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                stopping.cancel()
            if not waiting.done():
                # Stopped: the held wait returns `gone` once the group is signalled.
                state = await self._end(call, task_id, "stopped")
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(waiting, 30)
                return state
            try:
                result = waiting.result()
            except Exception:
                log.warning(
                    "Lost the sandbox of background task %s", task_id, exc_info=True
                )
                return "interrupted", None
            answer = (result or {}).get("state") if isinstance(result, dict) else None
            if answer == "exited":
                return "exited", int(result["exit_status"])
            if answer == "running":
                continue
            # `gone` with no signal from here, or `unknown`: something else ended it.
            return "interrupted", None

    async def _end(
        self, call: SandboxCall, task_id: str, state: str
    ) -> tuple[str, int | None]:
        try:
            await call("signal_task", {"id": task_id})
        except Exception:
            log.warning("Could not signal background task %s", task_id, exc_info=True)
            return "interrupted", None
        return state, None


background_task_registry = BackgroundTaskRegistry()


def holding(agent: Any) -> AbstractAsyncContextManager[Any] | None:
    """A pin on the pooled agent that started a command; None for an agent not in the pool."""
    from .project_agent import project_agent_pool

    return _PoolHold(project_agent_pool, agent)


class _PoolHold:
    """`TTLPool.hold`, or nothing when the agent is not pooled (a stateless or test agent)."""

    def __init__(self, pool: Any, agent: Any) -> None:
        self._pool = pool
        self._agent = agent
        self._held: AbstractAsyncContextManager[Any] | None = None

    async def __aenter__(self) -> None:
        held = self._pool.hold(self._agent)
        try:
            await held.__aenter__()
        except LookupError:
            return
        self._held = held

    async def __aexit__(self, *exc: Any) -> None:
        if self._held is not None:
            await self._held.__aexit__(None, None, None)
