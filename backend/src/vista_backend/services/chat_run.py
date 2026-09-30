"""
Chat turns that outlive the page watching them (openspec change background-chat-runs).

A turn is a `ChatRun`: a background task that drives `ProjectAgent.run_stream`,
an append-only list of numbered events, and subscribers that replay that list
from any point and then follow it live. Closing a subscriber touches nothing
else, so leaving the chat page never cancels a turn. Only `stop` does.

The registry is in-process, like the agent pool: one backend, one researcher.
The run's outcome is written to the `chat_session` row by the run itself (its
model history, its `run_state`, and its compacted events), so a turn nobody
watched is still there when the conversation is next opened.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.agents import (
    McpElicitationEvent,
    ProjectAgentResult,
    ProjectAgentResultEvent,
    RunCapture,
)
from ..db.db import get_engine
from ..db.schemas import ChatSessionTable
from ..utils.misc import now_iso
from . import chat_session as chat_session_service
from .project_agent import (
    ProjectAgentKey,
    project_agent_pool,
    register_elicitation,
    resolve_elicitation,
)
from .run_history import (
    FAILED_NOTE,
    INTERRUPTED_NOTE,
    STOPPED_NOTE,
    trim_partial_history,
)

log = logging.getLogger(__name__)

_EVENT_ADAPTER = TypeAdapter(Any)

_END_NOTES = {
    "stopped": STOPPED_NOTE,
    "interrupted": INTERRUPTED_NOTE,
    "failed": FAILED_NOTE,
}

# Outcomes the researcher has not yet seen. A stop is their own action, so it
# leaves no dot.
_UNSEEN_STATES = {"done", "failed", "interrupted"}


class RunBusy(Exception):
    """The conversation already has an active run."""


def session_factory() -> AsyncSession:
    """
    The session a run reads and writes through.

    A module-level factory rather than the request's session, because the run
    outlives the request, and so tests can point it at their own engine. Same
    shape as `api.debate.stream_session_factory`.
    """
    return AsyncSession(get_engine())


@dataclass
class RunEvent:
    seq: int
    """ 1-based position in the run, sent as the SSE `id:`. """
    kind: str
    """ The SSE event name, e.g. `part_start`, `log`, `run_started`. """
    data: str
    """ The JSON payload. """
    prompt_id: str | None = None
    """ The elicitation id, on a prompt event or its `prompt_resolved`. """


@dataclass
class ChatRun:
    chat_session_id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID
    agent_key: ProjectAgentKey
    user_prompt: str
    prior_history: list[ModelMessage]
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: str = "running"
    events: list[RunEvent] = field(default_factory=list)
    finished: bool = False
    stop_reason: str | None = None
    result: ProjectAgentResult | None = None
    task: asyncio.Task | None = None
    capture: RunCapture = field(default_factory=RunCapture)
    pending: set[str] = field(default_factory=set)
    """ Ids of prompts waiting for the researcher; non-empty means the run "needs you". """
    resolved: set[str] = field(default_factory=set)
    _cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    _started: asyncio.Event = field(default_factory=asyncio.Event)

    async def append(
        self, kind: str, data: str, prompt_id: str | None = None
    ) -> RunEvent:
        async with self._cond:
            event = RunEvent(
                seq=len(self.events) + 1, kind=kind, data=data, prompt_id=prompt_id
            )
            self.events.append(event)
            if kind in PROMPT_EVENT_KINDS and prompt_id is not None:
                self.pending.add(prompt_id)
            elif kind == "prompt_resolved" and prompt_id is not None:
                self.pending.discard(prompt_id)
                self.resolved.add(prompt_id)
            self._cond.notify_all()
        return event

    async def _finish(self) -> None:
        async with self._cond:
            self.finished = True
            self._cond.notify_all()

    async def subscribe(self, after: int = 0) -> AsyncIterator[RunEvent]:
        """
        Yield every event with `seq > after`, then follow live until the run ends.

        Any number of subscribers may watch one run. Each receives every event,
        and closing one affects nobody else.

        A prompt that was already answered when its event is read is skipped,
        together with its `prompt_resolved`: a view that arrives late has
        nothing to answer and nothing to clear. A view that saw the prompt live
        still gets the `prompt_resolved` that clears it.
        """
        index = max(after, 0)
        skipped: set[str] = set()
        while True:
            async with self._cond:
                await self._cond.wait_for(
                    lambda: index < len(self.events) or self.finished
                )
                batch = self.events[index:]
                resolved = set(self.resolved)
                done = self.finished
            for event in batch:
                if event.prompt_id is not None:
                    if event.kind in PROMPT_EVENT_KINDS and event.prompt_id in resolved:
                        skipped.add(event.prompt_id)
                        continue
                    if event.kind == "prompt_resolved" and event.prompt_id in skipped:
                        continue
                yield event
            index += len(batch)
            if done:
                return

    async def wait(self) -> None:
        """Wait until the run has ended and its outcome is saved."""
        async with self._cond:
            await self._cond.wait_for(lambda: self.finished)


class ChatRunRegistry:
    def __init__(self) -> None:
        self._runs: dict[uuid.UUID, ChatRun] = {}

    def get(self, chat_session_id: uuid.UUID) -> ChatRun | None:
        return self._runs.get(chat_session_id)

    def active(self) -> list[ChatRun]:
        return list(self._runs.values())

    async def start(
        self,
        *,
        chat_session_id: uuid.UUID,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        agent_key: ProjectAgentKey,
        user_prompt: str,
        prior_history: list[ModelMessage],
    ) -> ChatRun:
        """
        Start a turn in the background. Raises `RunBusy` if the conversation has one.

        Returns once the run's task is executing, so `stop` can never hit a task
        that has not yet entered its cleanup handler.
        """
        if chat_session_id in self._runs:
            raise RunBusy(str(chat_session_id))
        run = ChatRun(
            chat_session_id=chat_session_id,
            project_id=project_id,
            user_id=user_id,
            agent_key=agent_key,
            user_prompt=user_prompt,
            prior_history=prior_history,
        )
        self._runs[chat_session_id] = run
        try:
            await _write_row(run, state="running", unseen=False, events=None)
        except BaseException:
            self._runs.pop(chat_session_id, None)
            raise
        # The registry holds the run, and the run holds its task: asyncio keeps
        # only a weak reference to a running task.
        run.task = asyncio.create_task(self._drive(run))
        await run._started.wait()
        return run

    async def stop(
        self, chat_session_id: uuid.UUID, reason: str = "stopped", wait: bool = True
    ) -> bool:
        """Cancel a conversation's run. False when it has none."""
        run = self._runs.get(chat_session_id)
        if run is None or run.task is None:
            return False
        run.stop_reason = reason
        run.task.cancel()
        if wait:
            await run.wait()
        return True

    async def stop_all(self, reason: str = "interrupted", timeout: float = 5.0) -> None:
        """Stop every run, for shutdown. Waits at most `timeout` seconds in all."""
        runs = self.active()
        for run in runs:
            run.stop_reason = reason
            if run.task is not None:
                run.task.cancel()
        if runs:
            await asyncio.wait(
                [asyncio.ensure_future(run.wait()) for run in runs], timeout=timeout
            )

    async def _drive(self, run: ChatRun) -> None:
        run._started.set()
        history: list[ModelMessage] | None = None
        reraise: BaseException | None = None
        try:
            async with project_agent_pool.get(run.agent_key) as agent:
                async with session_factory() as db:
                    await run.append(
                        "run_started",
                        json.dumps(
                            {
                                "event_kind": "run_started",
                                "run_id": run.run_id,
                                "user_prompt": run.user_prompt,
                            }
                        ),
                    )
                    async for event in agent.run_stream(
                        user_prompt=run.user_prompt,
                        message_history=run.prior_history,
                        enable_elicitation=True,
                        db_session=db,
                        capture=run.capture,
                    ):
                        prompt_id = None
                        if isinstance(event, McpElicitationEvent):
                            # `resolve_prompt` answers it.
                            register_elicitation(event.elicitation_id, agent)
                            prompt_id = event.elicitation_id
                        if isinstance(event, ProjectAgentResultEvent):
                            run.result = event.result
                        await run.append(
                            event.event_kind,
                            _EVENT_ADAPTER.dump_json(event).decode(),
                            prompt_id=prompt_id,
                        )
            run.state = "done"
            if run.result is not None:
                history = [*run.prior_history, *run.result.new_messages]
        except asyncio.CancelledError as cancelled:
            run.state = run.stop_reason or "interrupted"
            if run.stop_reason is None:
                reraise = (
                    cancelled  # Cancelled from outside (loop shutdown), not by `stop`.
                )
        except Exception as exc:
            log.exception("Chat run %s failed", run.run_id)
            run.state = "failed"
            await run.append(
                "log",
                json.dumps(
                    {
                        "event_kind": "log",
                        "level": "ERROR",
                        "area": "Agent",
                        "message": f"Turn failed: {exc}",
                    }
                ),
            )

        if history is None:
            history = trim_partial_history(
                run.prior_history,
                run.capture.messages,
                user_prompt=run.user_prompt,
                note=_END_NOTES.get(run.state, FAILED_NOTE),
            )
        # `run_finished` goes out only after the outcome is saved and the run has
        # left the registry. A page that acknowledges the run the moment it sees
        # this event then finds the row final, and a fetch of the conversation
        # no longer reports a run in progress.
        finished_data = json.dumps({"event_kind": "run_finished", "state": run.state})
        try:
            await asyncio.shield(
                _write_row(
                    run,
                    state=run.state,
                    unseen=run.state in _UNSEEN_STATES,
                    events=compact_events(
                        [
                            *run.events,
                            RunEvent(
                                seq=len(run.events) + 1,
                                kind="run_finished",
                                data=finished_data,
                            ),
                        ]
                    ),
                    history=history,
                )
            )
        except Exception:
            log.exception("Could not save chat run %s", run.run_id)
        finally:
            # `start` keeps refusing a second run until the outcome is saved, or
            # the new turn would read stale history.
            self._runs.pop(run.chat_session_id, None)
            await run.append("run_finished", finished_data)
            await run._finish()
        if reraise is not None:
            raise reraise


async def resolve_prompt(
    elicitation_id: str,
    action: Literal["accept", "decline", "cancel"],
    content: dict[str, Any] | None = None,
) -> bool:
    """
    Answer a pending prompt (tool approval, form or URL elicitation).

    Returns False when no such prompt is waiting, such as a second view
    answering after the first. On success, appends `prompt_resolved` so every
    other view clears it. The answer itself goes only to the agent, never into
    the run's events.
    """
    if not resolve_elicitation(elicitation_id, action, content):
        return False
    for run in chat_run_registry.active():
        if elicitation_id in run.pending:
            await run.append(
                "prompt_resolved",
                json.dumps(
                    {"event_kind": "prompt_resolved", "elicitation_id": elicitation_id}
                ),
                prompt_id=elicitation_id,
            )
    return True


async def _write_row(
    run: ChatRun,
    *,
    state: str,
    unseen: bool,
    events: list[dict[str, Any]] | None,
    history: list[ModelMessage] | None = None,
) -> None:
    async with session_factory() as db:
        row = await chat_session_service.get_chat_session_optional(
            db,
            project_id=run.project_id,
            user_id=run.user_id,
            chat_session_id=run.chat_session_id,
        )
        if row is None:  # The conversation was deleted while the run was going.
            return
        if history is not None:
            row.message_history = chat_session_service.dump_message_history(history)
        row.run_state = state
        row.run_unseen = unseen
        row.run_events = events
        row.updated_at = now_iso()
        db.add(row)
        await db.commit()


def live_status(run: ChatRun | None) -> str | None:
    """`working` or `needs_you` for an active run, None when there is none."""
    if run is None:
        return None
    return "needs_you" if run.pending else "working"


def row_status(row: ChatSessionTable) -> str:
    """
    A conversation's status as the list shows it, from its saved row and live run.

    `done`, `failed` and `interrupted` are reported only while unseen. A `stopped`
    turn leaves no dot, since the researcher did it themselves.
    """
    live = live_status(chat_run_registry.get(row.id))
    if live is not None:
        return live
    if row.run_state == "running":
        return "working"  # Saved as running a moment before the run registered.
    if row.run_state in _UNSEEN_STATES and row.run_unseen:
        return row.run_state
    return "idle"


async def sweep_interrupted_runs() -> int:
    """
    At startup, turn every `running` row into `interrupted` and unseen.

    No run survives a restart, so a row still saying `running` belongs to a turn
    that died with the previous process (design D4).
    """
    async with session_factory() as db:
        rows = (
            await db.exec(
                select(ChatSessionTable).where(ChatSessionTable.run_state == "running")
            )
        ).all()
        for row in rows:
            row.run_state = "interrupted"
            row.run_unseen = True
            db.add(row)
        await db.commit()
    return len(rows)


def compact_events(events: list[RunEvent]) -> list[dict[str, Any]]:
    """
    Shrink a run's events for storage (design D7).

    Each part's start, deltas and end become one `part_start` carrying the final
    part, followed by its `part_end`: the shape campaign progress already emits,
    which the page draws. Prompt events are dropped, since answers must never be
    stored and a stored prompt could never be answered anyway.
    """
    out: list[dict[str, Any]] = []
    open_parts: dict[int, dict[str, Any]] = {}
    for event in events:
        if event.kind in PROMPT_EVENT_KINDS or event.kind == "prompt_resolved":
            continue
        if event.kind == "part_delta":
            continue
        data = json.loads(event.data)
        entry = {"event": event.kind, "data": data}
        if event.kind == "part_start":
            open_parts[data["index"]] = entry
        elif event.kind == "part_end":
            start = open_parts.pop(data["index"], None)
            if start is not None:
                start["data"]["part"] = data["part"]
        out.append(entry)
    return out


PROMPT_EVENT_KINDS = frozenset(
    {"mcp_form_elicitation", "mcp_url_elicitation", "mcp_tool_approval"}
)

chat_run_registry = ChatRunRegistry()
