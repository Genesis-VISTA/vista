"""
Side effects in a Slack turn wait for the requester's Confirm.

`SlackGateCapability` sits on every `ProjectAgent`, and does nothing unless the
run in progress came from Slack. Then a tool that is not declared read-only
(MCP's `readOnlyHint`, or the same annotation on a native tool) is first
checked against the lock and limits — a refusal is returned to the model as
the tool's result, with no confirmation offered — and then deferred with
`ApprovalRequired`. The agent's existing approval path emits
`mcp_tool_approval`, which the Slack relay shows as a Confirm/Cancel card.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ApprovalRequired
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

log = logging.getLogger(__name__)

READ_ONLY = {"annotations": {"readOnlyHint": True}}
""" `metadata=` for a native tool that only reads, so Slack runs it unconfirmed. """

Refusal = Callable[[uuid.UUID, str, dict[str, Any]], Awaitable[str | None]]
""" (user id, tool name, args) → why the call may not go ahead, or None. """


def is_read_only(tool_def: ToolDefinition) -> bool:
    annotations = (tool_def.metadata or {}).get("annotations") or {}
    return annotations.get("readOnlyHint") is True


async def no_refusal(user_id: uuid.UUID, tool_name: str, args: dict[str, Any]) -> None:
    return None


_refusal: Refusal = no_refusal


def set_refusal(check: Refusal) -> None:
    """Install the lock-and-limits check; the Slack service does, when it starts."""
    global _refusal
    _refusal = check


async def refusal(
    user_id: uuid.UUID, tool_name: str, args: dict[str, Any]
) -> str | None:
    return await _refusal(user_id, tool_name, args)


class SlackGateCapability(AbstractCapability):
    def __init__(
        self,
        *,
        channel: Callable[[], str],
        user_id: uuid.UUID,
    ) -> None:
        self._channel = channel
        self._user_id = user_id

    async def wrap_tool_execute(
        self,
        ctx: RunContext,
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: Any,
        handler: Any,
    ) -> Any:
        if self._channel() != "slack" or is_read_only(tool_def):
            return await handler(args)
        # Confirmed: the Confirm handler rechecked the lock and limits.
        if getattr(ctx, "tool_call_approved", False):
            return await handler(args)
        why = await refusal(
            self._user_id, tool_def.name, args if isinstance(args, dict) else {}
        )
        if why is not None:
            log.info("Refused %s from Slack: %s", tool_def.name, why)
            from . import audit  # Here, as the agent imports this module.

            await audit.record(
                "refused",
                user_id=self._user_id,
                tool_name=tool_def.name,
                detail={"args": args, "reason": why},
            )
            return f"Not done: {why}"
        raise ApprovalRequired()


SLACK_INSTRUCTIONS = """\
This conversation is happening in Slack, probably on a phone.

- Keep answers short: lead with the answer in a sentence or two. Details can follow.
- Never ask for passwords, MFA codes, tokens or any other credential, and never \
accept one if offered. Logins to clusters happen only in the VISTA desktop app; if \
an action needs one, say it must be done from the desktop.
- Anything that changes something (submitting or cancelling a job, writing files, \
running commands) is shown to the researcher for confirmation before it runs. If \
they decline, don't try again unless they ask.
"""
