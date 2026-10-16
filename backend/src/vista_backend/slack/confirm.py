"""
Confirm/Cancel cards for side effects requested from Slack.

The gate (`slack/gate.py`) defers a side effect; the agent emits
`mcp_tool_approval`; `Confirmations.offer` posts it in the thread as a card
with what would actually happen. Only the Slack user who asked can answer it.
Confirm checks the lock and limits again before the run is told to go ahead;
an unanswered card is declined after `confirmation_ttl` (10 minutes).

Every card is also a `slack_pending` row, so the outcome survives a restart
and a card left open when VISTA quit is marked expired the next time it starts.
Button values carry only the elicitation id: nothing in a card is trusted.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import select

from ..config import settings
from ..db.schemas import ChatSessionTable, SlackPendingTable
from ..services import chat_run as chat_run_module
from . import format, gate
from .events import Action
from .relay import Place, step_label
from .store import now
from .transport import SlackCallError, SlackTransport

log = logging.getLogger(__name__)

CONFIRM = "vista_confirm"
CANCEL = "vista_cancel"

Preview = Callable[[uuid.UUID, str, dict[str, Any]], Awaitable[dict[str, str]]]
""" (user id, tool name, args) → card fields describing what would really happen. """


async def no_preview(
    user_id: uuid.UUID, tool: str, args: dict[str, Any]
) -> dict[str, str]:
    return {}


_preview: Preview = no_preview


def set_preview(preview: Preview) -> None:
    """Install the job preview (`preview_hpc_job`); the Slack service does."""
    global _preview
    _preview = preview


def _tail(tool_name: str) -> str:
    return tool_name.split("__")[-1]


def fields_for(tool_name: str, args: dict[str, Any]) -> dict[str, str]:
    """What the card shows for a call, before any preview adds to it."""
    if _tail(tool_name) == "submit_hpc_job":
        script = str(args.get("job") or "?")
        if args.get("script_args"):
            script += f" {args['script_args']}"
        return {
            "System": str(args.get("cluster") or "default cluster"),
            "Nodes": str(args.get("node_count") or "job default"),
            "Walltime": str(args.get("duration") or "job default"),
            "Script": script,
        }
    if _tail(tool_name) == "cancel_hpc_job":
        return {
            "System": str(args.get("cluster") or "?"),
            "Job": str(args.get("job_id") or "?"),
        }
    return {
        key: format.truncate(
            value if isinstance(value, str) else json.dumps(value), 300, "…"
        )
        for key, value in list(args.items())[:8]
    }


def _field(name: str, value: str) -> dict[str, str]:
    return {"type": "mrkdwn", "text": f"*{name}*\n{format.truncate(value, 1900, '…')}"}


def card(pending: SlackPendingTable) -> format.Reply:
    summary = pending.summary or {}
    title = f"*Confirm: {step_label(pending.tool_name or '')}?*"
    fields = [_field(k, str(v)) for k, v in (summary.get("fields") or {}).items()]
    blocks: list[dict[str, Any]] = [format.section(title)]
    if fields:
        blocks.append({"type": "section", "fields": fields[:10]})
    if pending.outcome is None:
        minutes = round(settings.slack.confirmation_ttl / 60)
        blocks.append(
            format.context(
                f"Only <@{pending.slack_user_id}> can confirm. "
                f"Expires in {minutes} minutes."
            )
        )
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    format.button(
                        "Confirm", CONFIRM, pending.elicitation_id, "primary"
                    ),
                    format.button("Cancel", CANCEL, pending.elicitation_id, "danger"),
                ],
            }
        )
    else:
        blocks.append(format.context(_outcome_text(pending)))
    return format.Reply(text=title.strip("*"), blocks=blocks)


def _outcome_text(pending: SlackPendingTable) -> str:
    who = f"<@{pending.slack_user_id}>"
    reason = (pending.summary or {}).get("reason")
    return {
        "confirmed": f":white_check_mark: Confirmed by {who}.",
        "cancelled": f":no_entry_sign: Cancelled by {who}. Nothing was done.",
        "expired": ":hourglass: Expired. Nothing was done.",
        "refused": f":lock: Not done: {reason}",
    }.get(pending.outcome or "", "Closed.")


class Confirmations:
    def __init__(self, transport: SlackTransport) -> None:
        self.transport = transport
        self._timers: dict[str, asyncio.Task] = {}

    async def offer(
        self,
        event: chat_run_module.RunEvent,
        *,
        place: Place,
        team_id: str,
        chat_session_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Post the card for a `mcp_tool_approval` event; anything else is declined."""
        data = json.loads(event.data)
        elicitation_id = data.get("elicitation_id") or event.prompt_id
        if event.kind != "mcp_tool_approval" or not elicitation_id:
            # Forms are declined in Slack runs before they get here; a URL or
            # anything else needs the desktop.
            if elicitation_id:
                await chat_run_module.resolve_prompt(elicitation_id, "decline")
            return
        tool_name = data.get("tool_name") or ""
        args = data.get("args") or {}
        fields = fields_for(tool_name, args)
        try:
            fields.update(await _preview(user_id, tool_name, args))
        except Exception:
            log.exception("Could not preview %s for Slack", tool_name)
        created = now()
        pending = SlackPendingTable(
            elicitation_id=elicitation_id,
            kind="confirm",
            team_id=team_id,
            channel_id=place.channel,
            slack_user_id=place.slack_user_id,
            chat_session_id=chat_session_id,
            tool_name=tool_name,
            summary={"fields": fields, "args": args},
            created_at=created.isoformat(timespec="seconds"),
            expires_at=(
                created + timedelta(seconds=settings.slack.confirmation_ttl)
            ).isoformat(timespec="seconds"),
        )
        reply = card(pending)
        pending.message_ts = await self.transport.post(
            place.channel, reply.text, blocks=reply.blocks, thread_ts=place.thread_ts
        )
        async with chat_run_module.session_factory() as db:
            db.add(pending)
            await db.commit()
        self._timers[elicitation_id] = asyncio.create_task(
            self._expire_later(elicitation_id)
        )

    async def _expire_later(self, elicitation_id: str) -> None:
        await asyncio.sleep(settings.slack.confirmation_ttl)
        self._timers.pop(elicitation_id, None)
        await self.expire(elicitation_id)

    async def expire(self, elicitation_id: str) -> None:
        """Decline a card nobody answered in time."""
        await self._close(elicitation_id, "expired")

    async def on_action(self, action: Action) -> None:
        """A tap on Confirm or Cancel."""
        elicitation_id = action.value
        async with chat_run_module.session_factory() as db:
            pending = await db.get(SlackPendingTable, elicitation_id)
            chat = (
                await db.get(ChatSessionTable, pending.chat_session_id)
                if pending is not None and pending.chat_session_id
                else None
            )
            user_id = chat.user_id if chat is not None else None
        if pending is None or pending.team_id != action.team_id:
            await self._tell(action, "That request is no longer open.")
            return
        if action.user_id != pending.slack_user_id:
            await self._tell(
                action, f"Only <@{pending.slack_user_id}> can answer this request."
            )
            return
        if pending.outcome is not None:
            await self._tell(action, "That request is already closed.")
            return
        if now() >= datetime.fromisoformat(pending.expires_at):
            await self._close(elicitation_id, "expired")
            await self._tell(action, "That request expired. Nothing was done.")
            return
        if action.action_id == CANCEL:
            await self._close(elicitation_id, "cancelled")
            return
        if user_id is None:  # The conversation was deleted on the desktop.
            await self._close(elicitation_id, "expired")
            return
        why = await gate.refusal(
            user_id, pending.tool_name or "", (pending.summary or {}).get("args") or {}
        )
        if why is not None:
            await self._close(elicitation_id, "refused", reason=why)
            return
        await self._close(elicitation_id, "confirmed")

    async def _close(self, elicitation_id: str, outcome: str, reason: str = "") -> None:
        async with chat_run_module.session_factory() as db:
            pending = await db.get(SlackPendingTable, elicitation_id)
            if pending is None or pending.outcome is not None:
                return
            pending.outcome = outcome
            if reason:
                pending.summary = {**(pending.summary or {}), "reason": reason}
            db.add(pending)
            await db.commit()
            await db.refresh(pending)
        timer = self._timers.pop(elicitation_id, None)
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()
        action = "accept" if outcome == "confirmed" else "decline"
        answered = await chat_run_module.resolve_prompt(elicitation_id, action)
        if not answered and outcome == "confirmed":
            # The run is gone (VISTA restarted, or it was stopped): nothing ran.
            await self._set_outcome(pending, "expired")
        await self._redraw(pending)

    async def _set_outcome(self, pending: SlackPendingTable, outcome: str) -> None:
        async with chat_run_module.session_factory() as db:
            row = await db.get(SlackPendingTable, pending.elicitation_id)
            if row is not None:
                row.outcome = outcome
                db.add(row)
                await db.commit()
        pending.outcome = outcome

    async def _redraw(self, pending: SlackPendingTable) -> None:
        if pending.message_ts is None:
            return
        reply = card(pending)
        try:
            await self.transport.update(
                pending.channel_id, pending.message_ts, reply.text, blocks=reply.blocks
            )
        except SlackCallError as exc:
            log.info("Could not update a Slack confirmation card: %s", exc.error)

    async def _tell(self, action: Action, text: str) -> None:
        try:
            await self.transport.ephemeral(
                action.channel_id, action.user_id, text, thread_ts=action.thread_ts
            )
        except SlackCallError as exc:
            log.info("Could not answer a Slack tap: %s", exc.error)

    async def close_stale(self) -> int:
        """At start-up: no run survived, so every open card has expired."""
        async with chat_run_module.session_factory() as db:
            rows = list(
                (
                    await db.exec(
                        select(SlackPendingTable).where(
                            SlackPendingTable.outcome == None  # noqa: E711
                        )
                    )
                ).all()
            )
            for row in rows:
                row.outcome = "expired"
                db.add(row)
            await db.commit()
            for row in rows:
                await db.refresh(row)
        for row in rows:
            await self._redraw(row)
        return len(rows)

    def stop(self) -> None:
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
