"""
A chat run, shown in Slack as one message that is edited as the run goes.

The relay posts "Working on it…" in the thread, edits it with the step the
agent is on (at most every `progress_edit_interval` seconds, well under Slack's
rate limits), and when the run ends replaces it with the answer: a summary
first, the rest behind "Show more". If Slack refuses the edit, the answer is
posted instead; if it refuses the blocks, it goes as plain text. Nothing about
the answer's length can make it fail.

Afterwards the turn is added to the conversation's transcript, so the desktop
shows it, tagged with the run's id as the in-app chat tags its own bubbles.
"""

import json
import logging
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..config import settings
from ..db.schemas import ChatSessionTable, ChatTranscriptMessage, SlackDetailTable
from ..services import chat_run as chat_run_module
from ..services.chat_run import PROMPT_EVENT_KINDS, ChatRun, RunEvent
from . import format
from .store import now_iso
from .transport import SlackCallError, SlackTransport

log = logging.getLogger(__name__)

WORKING = "Working on it…"

# Slack refused the message itself; a plainer one will go through.
_REFUSED_CONTENT = {"msg_too_long", "invalid_blocks", "invalid_blocks_format"}

# The in-app chat's labels (`ui/lib/tool-labels.ts`), for the progress line.
STEP_LABELS = {
    "rag_search": "Searching the literature",
    "display_file": "Preparing a figure",
    "run_bash": "Running a command",
    "create_file": "Writing a file",
    "view": "Reading a file",
    "submit_hpc_job": "Submitting a job to the cluster",
    "submit_job": "Submitting a job to the cluster",
    "get_hpc_job_status": "Checking the job",
    "get_job_status": "Checking the job",
    "get_hpc_job_outputs": "Fetching job output",
    "get_hpc_queue_status": "Checking the queue",
    "list_my_recent_jobs": "Looking up your jobs",
    "preview_hpc_job": "Preparing the job",
}

_END_TEXT = {
    "failed": "Sorry, that turn failed before it finished. Try again, or check the desktop app.",
    "stopped": "That turn was stopped.",
    "interrupted": "VISTA quit before that turn finished. Ask again when it's back.",
}


def step_label(tool_name: str) -> str:
    tail = tool_name.split("__")[-1]
    return STEP_LABELS.get(tool_name) or STEP_LABELS.get(tail) or f"Running `{tail}`"


def final_text(result_data: dict[str, Any]) -> str:
    """The text of the turn's last model response, from an `agent_run_result` event."""
    messages = (result_data.get("result") or {}).get("new_messages") or []
    for message in reversed(messages):
        if message.get("kind") != "response":
            continue
        text = "".join(
            part.get("content") or ""
            for part in message.get("parts") or []
            if part.get("part_kind") == "text"
        )
        if text.strip():
            return text
    return ""


def _notes(result_data: dict[str, Any]) -> list[str]:
    warnings = (result_data.get("result") or {}).get("egress_warnings") or []
    return [
        f":warning: {w['message']}"
        for w in warnings
        if isinstance(w, dict) and w.get("message")
    ]


@dataclass
class Place:
    """Where a turn is shown: the thread, and who asked."""

    channel: str
    thread_ts: str
    slack_user_id: str


PromptHandler = Callable[[RunEvent], Awaitable[None]]


class RunRelay:
    def __init__(
        self,
        transport: SlackTransport,
        place: Place,
        *,
        on_prompt: PromptHandler | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.transport = transport
        self.place = place
        self.on_prompt = on_prompt
        self.clock = clock
        self.progress_ts: str | None = None
        self._last_edit = 0.0
        self._step: str | None = None

    async def start(self) -> None:
        """Post the progress message. Call before the run's events are read."""
        self.progress_ts = await self._post(format.plain(WORKING))
        self._last_edit = self.clock()

    async def follow(self, run: ChatRun) -> str:
        """Show `run` until it ends; returns the answer's Markdown ("" if none)."""
        if self.progress_ts is None:
            await self.start()
        result: dict[str, Any] = {}
        state = "done"
        async for event in run.subscribe():
            if event.kind in PROMPT_EVENT_KINDS:
                if self.on_prompt is not None:
                    await self.on_prompt(event)
                continue
            if event.kind == "function_tool_call":
                data = json.loads(event.data)
                name = (data.get("part") or {}).get("tool_name") or ""
                await self._progress(step_label(name))
            elif event.kind == "agent_run_result":
                result = json.loads(event.data)
            elif event.kind == "run_finished":
                state = json.loads(event.data).get("state") or "done"
        answer = final_text(result)
        if state != "done" or not answer:
            reply = format.plain(_END_TEXT.get(state, _END_TEXT["failed"]))
            if answer:  # Stopped after it had something to say.
                reply = format.answer(answer, notes=[_END_TEXT.get(state, "")])
        else:
            reply = format.answer(answer, notes=_notes(result))
        if reply.detail:
            reply = format.show_more_button(
                reply, await self._keep_detail(reply.detail)
            )
        await self._replace(reply)
        await record_turn(run, answer or reply.text)
        return answer

    async def _progress(self, step: str) -> None:
        if step == self._step:
            return
        self._step = step
        if self.clock() - self._last_edit < settings.slack.progress_edit_interval:
            return
        self._last_edit = self.clock()
        try:
            await self.transport.update(
                self.place.channel, self._ts(), f"{WORKING} _{step}…_"
            )
        except SlackCallError as exc:
            log.info("Could not update Slack progress: %s", exc.error)

    async def _replace(self, reply: format.Reply) -> None:
        try:
            await self.transport.update(
                self.place.channel, self._ts(), reply.text, blocks=reply.blocks
            )
            return
        except SlackCallError as exc:
            if exc.error in _REFUSED_CONTENT:
                try:
                    plain = format.plain(reply.text)
                    await self.transport.update(
                        self.place.channel, self._ts(), plain.text
                    )
                    return
                except SlackCallError:
                    pass
            log.info("Could not edit the Slack answer in place: %s", exc.error)
        await self._post(reply)

    async def _post(self, reply: format.Reply) -> str:
        try:
            return await self.transport.post(
                self.place.channel,
                reply.text,
                blocks=reply.blocks or None,
                thread_ts=self.place.thread_ts,
            )
        except SlackCallError as exc:
            if exc.error not in _REFUSED_CONTENT:
                raise
            return await self.transport.post(
                self.place.channel,
                format.plain(reply.text).text,
                thread_ts=self.place.thread_ts,
            )

    def _ts(self) -> str:
        assert self.progress_ts is not None
        return self.progress_ts

    async def _keep_detail(self, text: str) -> str:
        detail_id = secrets.token_urlsafe(12)
        async with chat_run_module.session_factory() as db:
            db.add(
                SlackDetailTable(
                    id=detail_id,
                    slack_user_id=self.place.slack_user_id,
                    text=text,
                    created_at=now_iso(),
                )
            )
            await db.commit()
        return detail_id


async def record_turn(run: ChatRun, answer: str) -> None:
    """Add the turn to the transcript the desktop shows, once."""
    async with chat_run_module.session_factory() as db:
        row = await db.get(ChatSessionTable, run.chat_session_id)
        if row is None:  # Deleted on the desktop meanwhile.
            return
        kept = [
            ChatTranscriptMessage.model_validate(m)
            for m in row.messages
            if (m.get("run_id") if isinstance(m, dict) else m.run_id) != run.run_id
        ]
        kept += [
            ChatTranscriptMessage(
                id=uuid.uuid4().hex,
                role="user",
                content=run.user_prompt,
                run_id=run.run_id,
            ),
            ChatTranscriptMessage(
                id=uuid.uuid4().hex, role="assistant", content=answer, run_id=run.run_id
            ),
        ]
        row.messages = [m.model_dump(mode="json") for m in kept]  # type: ignore[assignment]
        row.updated_at = now_iso()
        db.add(row)
        await db.commit()
