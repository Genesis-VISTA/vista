"""
A chat run, shown in Slack as one message that is edited as the run goes.

The relay posts "Working on it…" in the thread, edits it with the step the
agent is on (at most every `progress_edit_interval` seconds, well under Slack's
rate limits), and when the run ends replaces it with the answer: a summary
first, the rest behind "Show more". If Slack refuses the edit, the answer is
posted instead; if it refuses the blocks, it goes as plain text. Nothing about
the answer's length can make it fail.

Afterwards the turn is added to the conversation's transcript, so the desktop
shows it, tagged with the run's id as the in-app chat tags its own bubbles: the
researcher's messages the turn took, then the answer.

Every turn of a Slack conversation is followed, including those VISTA starts
itself from a task result. Each inbox item a turn takes is passed to
`on_taken`, which swaps a waiting message's ⏳ for ✅.

The files the turn displayed (`display_file`: a figure, a log) are attached in
the thread after the answer, at most `MAX_ATTACHMENTS`, so an answer that says
"shown above" has something above it.
"""

import asyncio
import json
import logging
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pathlib import Path

from sqlmodel import col, select

from ..agents.agents import agent_volume_root
from ..config import settings
from ..db.schemas import (
    ChatSessionTable,
    ChatTranscriptMessage,
    DisplayedFileTable,
    SlackDetailTable,
)
from ..services import chat_run as chat_run_module
from ..services.chat_run import PROMPT_EVENT_KINDS, ChatRun, RunEvent
from . import format
from .store import now_iso
from .transport import SlackCallError, SlackTransport

log = logging.getLogger(__name__)

WORKING = "Working on it…"

MAX_ATTACHMENTS = 10
""" Files a turn displayed that are attached in its thread; past this the thread says how many more. """
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
""" Larger files are left for the desktop app rather than uploaded from a laptop's connection. """

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
    "cancel_hpc_job": "Cancelling the job",
    "agenthpc_submit_parameter_set": "Submitting a parameter set",
    "agenthpc_cancel_job": "Cancelling the job",
    "agenthpc_cancel_all_pending": "Cancelling every pending job",
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
TakenHandler = Callable[[dict[str, Any]], Awaitable[None]]
""" Gets each `inbox_taken` event's data: `item_id`, `kind`, `text`, `payload`. """
WithdrawnHandler = Callable[[list[int]], Awaitable[None]]
""" Gets the ids of the queued messages a Stop withdrew, unanswered. """

WITHDRAWN = "Messages sent while it ran weren't answered. Send them again if you still need them."


class RunRelay:
    def __init__(
        self,
        transport: SlackTransport,
        place: Place,
        *,
        on_prompt: PromptHandler | None = None,
        on_taken: TakenHandler | None = None,
        on_withdrawn: WithdrawnHandler | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.transport = transport
        self.place = place
        self.on_prompt = on_prompt
        self.on_taken = on_taken
        self.on_withdrawn = on_withdrawn
        self.clock = clock
        self.progress_ts: str | None = None
        self._started = False
        self._last_edit = 0.0
        self._step: str | None = None

    async def start(self) -> None:
        """Post the progress message. Call before the run's events are read."""
        try:
            self.progress_ts = await self._post(format.plain(WORKING))
        except SlackCallError as exc:
            # The answer is posted on its own at the end instead.
            log.info("Could not post Slack progress: %s", exc.error)
        self._last_edit = self.clock()
        self._started = True

    async def follow(self, run: ChatRun) -> str:
        """Show `run` until it ends; returns the answer's Markdown ("" if none)."""
        if not self._started:
            await self.start()
        result: dict[str, Any] = {}
        state = "done"
        taken: list[dict[str, Any]] = []
        withdrawn: list[int] = []
        async for event in run.subscribe():
            if event.kind in PROMPT_EVENT_KINDS:
                await self._prompt(event)
                continue
            if event.kind == "inbox_taken":
                item = json.loads(event.data)
                taken.append(item)
                if self.on_taken is not None:
                    try:
                        await self.on_taken(item)
                    except Exception:
                        log.exception("Could not mark a taken Slack message")
            elif event.kind == "function_tool_call":
                data = json.loads(event.data)
                name = (data.get("part") or {}).get("tool_name") or ""
                await self._progress(step_label(name))
            elif event.kind == "agent_run_result":
                result = json.loads(event.data)
            elif event.kind == "run_finished":
                finished = json.loads(event.data)
                state = finished.get("state") or "done"
                withdrawn = [w["id"] for w in finished.get("withdrawn") or []]
        answer = final_text(result)
        if state != "done" or not answer:
            end = _END_TEXT.get(state, _END_TEXT["failed"])
            notes = [end, WITHDRAWN] if withdrawn else [end]
            reply = format.plain(" ".join(notes))
            if answer:  # Stopped after it had something to say.
                reply = format.answer(answer, notes=notes)
        else:
            reply = format.answer(answer, notes=_notes(result))
        if run.origin == "task":
            # VISTA started this turn (a watch's or a command's result): the user
            # did not just ask, so the answer @mentions them.
            reply = format.mention(reply, self.place.slack_user_id)
        if reply.detail:
            reply = format.show_more_button(
                reply, await self._keep_detail(reply.full or reply.detail)
            )
        await self._replace(reply)
        await self._attach(run)
        if withdrawn and self.on_withdrawn is not None:
            try:
                await self.on_withdrawn(withdrawn)
            except Exception:
                log.exception("Could not unmark withdrawn Slack messages")
        await record_turn(run, answer or reply.text, prompts=_prompts(run, taken))
        return answer

    async def _prompt(self, event: RunEvent) -> None:
        """Show a prompt; one that cannot be shown is declined, so the run is not stuck on it."""
        try:
            if self.on_prompt is None:
                raise RuntimeError("no way to show prompts here")
            await self.on_prompt(event)
        except Exception:
            log.exception("Could not show a prompt in Slack; declining it")
            if event.prompt_id:
                await chat_run_module.resolve_prompt(event.prompt_id, "decline")

    async def _progress(self, step: str) -> None:
        if step == self._step:
            return
        self._step = step
        if self.clock() - self._last_edit < settings.slack.progress_edit_interval:
            return
        if self.progress_ts is None:
            return
        self._last_edit = self.clock()
        try:
            await self.transport.update(
                self.place.channel, self._ts(), f"{WORKING} _{step}…_"
            )
        except SlackCallError as exc:
            log.info("Could not update Slack progress: %s", exc.error)

    async def _replace(self, reply: format.Reply) -> None:
        if self.progress_ts is None:
            await self._post(reply)
            return
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

    async def _attach(self, run: ChatRun) -> None:
        """Upload the files the run displayed into the thread; say so for any that cannot go."""
        try:
            async with chat_run_module.session_factory() as db:
                rows = (
                    await db.exec(
                        select(DisplayedFileTable)
                        .where(DisplayedFileTable.run_id == run.run_id)
                        .order_by(col(DisplayedFileTable.shown_at))
                    )
                ).all()
                shown = list(dict.fromkeys((r.kind, r.path) for r in rows))
                await db.commit()
        except Exception:
            log.exception("Could not list run %s's displayed files", run.run_id)
            return
        folders = {
            "outputs": agent_volume_root(run.project_id) / "data" / "output",
            "uploads": agent_volume_root(run.project_id) / "data" / "uploads",
        }
        for kind, rel in shown[:MAX_ATTACHMENTS]:
            name = Path(rel).name or rel
            try:
                content = await asyncio.to_thread(_read_shown, folders.get(kind), rel)
                await self.transport.upload(
                    self.place.channel,
                    filename=name,
                    content=content,
                    title=name,
                    thread_ts=self.place.thread_ts,
                )
            except (OSError, ValueError, SlackCallError) as exc:
                log.info("Could not attach %s in Slack: %s", rel, exc)
                await self._say(
                    f"Couldn't attach `{name}` here. Open it in the VISTA desktop app."
                )
        more = len(shown) - MAX_ATTACHMENTS
        if more > 0:
            files = "file" if more == 1 else "files"
            await self._say(
                f"{more} more {files} from this answer are in the VISTA desktop app."
            )

    async def _say(self, text: str) -> None:
        try:
            await self._post(format.plain(text))
        except SlackCallError as exc:
            log.info("Could not post in Slack: %s", exc.error)

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


def _prompts(run: ChatRun, taken: list[dict[str, Any]]) -> list[str]:
    """
    The researcher's messages this turn took, in order. A turn that took no
    items was started directly with its prompt (tests); one that took only task
    results has none.
    """
    if not taken:
        return [run.user_prompt] if run.origin == "researcher" else []
    return [i.get("text") or "" for i in taken if i.get("kind") == "message"]


async def record_turn(
    run: ChatRun, answer: str, *, prompts: list[str] | None = None
) -> None:
    """
    Add the turn to the transcript the desktop shows: `prompts` (default the
    run's prompt) as user bubbles, then `answer`. Once: whatever the transcript
    already holds for this run is replaced.
    """
    if prompts is None:
        prompts = [run.user_prompt]
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
                id=uuid.uuid4().hex, role="user", content=prompt, run_id=run.run_id
            )
            for prompt in prompts
        ]
        kept += [
            ChatTranscriptMessage(
                id=uuid.uuid4().hex, role="assistant", content=answer, run_id=run.run_id
            ),
        ]
        row.messages = [m.model_dump(mode="json") for m in kept]  # type: ignore[assignment]
        row.updated_at = now_iso()
        db.add(row)
        await db.commit()


def _read_shown(folder: Path | None, rel: str) -> bytes:
    """A displayed file's bytes, refusing anything outside its folder or too large to send."""
    if folder is None:
        raise ValueError(f"no folder for {rel}")
    path = (folder / rel).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise ValueError(f"{rel} is outside its folder")
    if path.stat().st_size > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"{rel} is too large to attach")
    return path.read_bytes()
