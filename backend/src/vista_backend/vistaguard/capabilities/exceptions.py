"""
Exceptions raised by VISTAGuard capabilities.

These are control-flow signals, not error conditions: a capability hook
raises one to abort the agent run from *inside* PydanticAI's hook
machinery, and `agents.py` catches it in the `run_stream` wrapper to
emit a structured refusal in place of the agent's normal output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..gates.base import GateDecision


class VistaGuardDeny(Exception):
    """
    Raised by a capability's ``before_run`` hook to abort the run.

    Carries the denying ``GateDecision`` so the caller can surface the
    reason (and incident level) without re-running the gate. The
    canonical raiser is `G1PromptCapability.before_run`, which raises
    this when `VistaGuardSidecar.evaluate_user_prompt` returns a deny
    decision; `ProjectAgent.run_stream` catches it and yields the
    synthetic terminal event with empty ``new_messages`` and a zeroed
    ``RunUsage``.
    """

    def __init__(self, decision: GateDecision) -> None:
        self.decision = decision
        super().__init__(decision.reason)
