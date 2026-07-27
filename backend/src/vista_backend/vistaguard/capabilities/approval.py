"""
`VistaGuardApprovalCapability` -- human-in-the-loop approval for
high-stakes tool calls (Phase 3.5, issue R6).

PydanticAI natively supports human-in-the-loop tool approval: a tool
registered with ``requires_approval=True`` (or one that raises
``ApprovalRequired``) pauses the run and surfaces a
`DeferredToolRequests`. This capability implements the
``handle_deferred_tool_calls`` hook to resolve those approvals *inline*
during the run -- it asks the user (through a caller-supplied
``request_approval`` callback) and returns a `DeferredToolResults`
approving or denying each call.

VISTA wires ``request_approval`` to the same event stream +
``resolve_elicitation(action, content)`` surface the frontend already
uses, so no new client API is required (Phase 3.5 R6 AC2). This is the
surface G5's ``submit_hpc_job`` (Phase 4) will plug into via
``@tool(requires_approval=True)`` instead of a bespoke flow.

Unlike the gate capabilities, this one is pure infrastructure: it has
no `Gate` and no per-session state, so it subclasses
`AbstractCapability` directly rather than `VistaGuardCapability`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai import DeferredToolResults, ToolApproved, ToolDenied
from pydantic_ai.capabilities import AbstractCapability

if TYPE_CHECKING:
    from pydantic_ai import DeferredToolRequests, RunContext
    from pydantic_ai.tools import DeferredToolApprovalResult


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApprovalOutcome:
    """
    The result of asking the user to approve one tool call.
    """

    approved: bool
    override_args: dict[str, Any] | None = None
    message: str = "Tool call denied by user."


#: A caller-supplied coroutine that asks the user to approve a single
#: tool call and returns the `ApprovalOutcome`. VISTA's `ProjectAgent`
#: backs this with the elicitation event stream + `resolve_elicitation`.
RequestApprovalFn = Callable[..., Awaitable[ApprovalOutcome]]


class VistaGuardApprovalCapability(AbstractCapability):
    """
    Resolve ``requires_approval`` tool calls via a human-in-the-loop
    callback.

    The capability is inert on a normal run: ``handle_deferred_tool_calls``
    fires only when a tool actually defers for approval, so adding it to
    an agent that has no ``requires_approval`` tools changes nothing.
    """

    def __init__(self, *, request_approval: RequestApprovalFn) -> None:
        self._request_approval = request_approval

    async def handle_deferred_tool_calls(
        self,
        ctx: RunContext,
        requests: DeferredToolRequests,
    ) -> DeferredToolResults | None:
        """
        Resolve each approval-required call by asking the user.
        """
        if not requests.approvals:
            return None

        approvals: dict[str, DeferredToolApprovalResult | bool] = {}
        for call in requests.approvals:
            try:
                outcome = await self._request_approval(
                    tool_name=call.tool_name,
                    tool_call_id=call.tool_call_id,
                    args=call.args,
                )
            except Exception as exc:  # noqa: BLE001 -- approval must fail closed
                logger.warning(
                    "VISTAGuard approval: request failed for %r (%s: %s); default-deny",
                    call.tool_name,
                    type(exc).__name__,
                    exc,
                )
                approvals[call.tool_call_id] = ToolDenied(
                    message=f"Approval failed for {call.tool_name!r}; denied."
                )
                continue

            if outcome.approved:
                approvals[call.tool_call_id] = ToolApproved(
                    override_args=outcome.override_args
                )
            else:
                approvals[call.tool_call_id] = ToolDenied(message=outcome.message)

        return DeferredToolResults(approvals=approvals)


__all__ = [
    "ApprovalOutcome",
    "RequestApprovalFn",
    "VistaGuardApprovalCapability",
]
