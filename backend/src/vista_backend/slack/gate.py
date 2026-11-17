"""
A job submission in a Slack turn waits for the requester's Confirm.

`SlackGateCapability` sits on every `ProjectAgent`, and does nothing unless the
run in progress came from Slack. Then a tool that is not declared read-only
(MCP's `readOnlyHint`, or the same annotation on a native tool) is checked
against the lock and limits: a refusal is returned to the model as the tool's
result, with no confirmation offered. A job submission or cancellation
(`CONFIRMED`) is then
deferred with `ApprovalRequired`; the agent's existing approval path emits
`mcp_tool_approval`, which the Slack relay shows as a Confirm/Cancel card.
Every other tool runs once it passes the check, so shell commands and file
writes don't stop a Slack turn at each step.
"""

import json
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

CONFIRMED: frozenset[str] = frozenset(
    {
        "submit_hpc_job",
        "agenthpc_submit_parameter_set",
        "cancel_hpc_job",
        "agenthpc_cancel_job",
        "agenthpc_cancel_all_pending",
    }
)
""" Tools a Slack turn asks to confirm, by name without the MCP server's prefix:
job submissions and cancellations. Everything else that passes the check runs. """

Refusal = Callable[..., Awaitable[str | None]]
""" (tool name, args, *, chat_session_id) → why the call may not go ahead, or None. """


def args_dict(args: Any) -> dict[str, Any] | None:
    """
    A tool call's arguments as a dict. OpenAI-compatible providers send them as
    a JSON string; left as one, an approval card, its preview and the Confirm
    recheck saw no arguments at all (the blank Confirm card).
    """
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except ValueError:
            return None
    return args if isinstance(args, dict) else None


def is_read_only(tool_def: ToolDefinition) -> bool:
    annotations = (tool_def.metadata or {}).get("annotations") or {}
    return annotations.get("readOnlyHint") is True


async def no_refusal(tool_name: str, args: dict[str, Any], **_: Any) -> None:
    return None


_refusal: Refusal = no_refusal


def set_refusal(check: Refusal) -> None:
    """Install the lock-and-limits check; the Slack service does, when it starts."""
    global _refusal
    _refusal = check


async def refusal(
    tool_name: str, args: dict[str, Any], *, chat_session_id: uuid.UUID | None = None
) -> str | None:
    return await _refusal(tool_name, args, chat_session_id=chat_session_id)


class SlackGateCapability(AbstractCapability):
    def __init__(
        self,
        *,
        channel: Callable[[], str],
        chat_session_id: Callable[[], uuid.UUID | None] = lambda: None,
    ) -> None:
        self._channel = channel
        self._chat_session_id = chat_session_id

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
            tool_def.name,
            args_dict(args) or {},
            chat_session_id=self._chat_session_id(),
        )
        if why is not None:
            log.info("Refused %s from Slack: %s", tool_def.name, why)
            from . import audit  # Here, as the agent imports this module.

            await audit.record(
                "refused",
                tool_name=tool_def.name,
                detail={"args": args, "reason": why},
            )
            return f"Not done: {why}"
        if tool_def.name.split("__")[-1] not in CONFIRMED:
            return await handler(args)
        raise ApprovalRequired()


SLACK_INSTRUCTIONS = """\
This conversation is happening in Slack, probably on a phone.

- Keep answers short: lead with the answer in a sentence or two. Details can follow.
- Never ask for passwords, MFA codes, tokens or any other credential, and never \
accept one if offered. Logins to clusters happen only in the VISTA desktop app; if \
an action needs one, say it must be done from the desktop.
- Submitting or cancelling an HPC job is shown to the researcher for confirmation \
before it happens. If they decline, don't try again unless they ask.
- A job submitted here is watched for you: you will be told when it starts and \
when it ends, so don't call watch_hpc_job for it or poll it.
"""
