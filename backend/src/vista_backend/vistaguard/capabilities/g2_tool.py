"""
`G2ToolCapability` -- the PydanticAI-hook adapter for the G2 Tool Gate.

G2 is the highest-test-surface gate (allow-list, ETDI descriptor
hashing, JSON-Schema validation, capability-taint + high-stakes guard,
and the Minimize-and-Sanitize slow tier). The migration spreads those
checks across every tool-related hook, delegating all policy to the
existing `G2ToolGate` (whose 67 unit tests are untouched):

| G2 check                       | Hook                          |
| ------------------------------ | ----------------------------- |
| Allow-list (fnmatch patterns)  | ``prepare_tools`` (drop tool) |
| ETDI descriptor hash           | ``prepare_tools`` (drop tool) |
| JSON-Schema validation         | ``before_tool_validate`` (*)  |
| Capability-taint + high-stakes | ``before_tool_execute``       |
| Minimize on input args         | ``before_tool_execute``       |
| Sanitize on tool returns       | ``after_tool_execute``        |

(*) PydanticAI performs the actual schema check; the hook records the
validation attempt for provenance. The gate's own schema check still
runs in ``before_tool_execute`` (it calls ``check_fast``) as a
belt-and-suspenders safety net, matching the gate's documented posture.

Unlike G3, G2 deliberately does **not** restrict its hooks to a tool
filter -- every check applies to every tool. (G3 owns the
``rag_search``-only hooks.)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.exceptions import SkipToolExecution

from ..gates.base import GateContext
from .base import VistaGuardCapability
from ...utils.misc import tool_allowed

if TYPE_CHECKING:
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolDefinition

    from ..gates.g2_tool import G2ToolGate


logger = logging.getLogger(__name__)


class G2ToolCapability(VistaGuardCapability):
    """
    G2 Tool Gate exposed as a PydanticAI capability.

    Registers ``prepare_tools``, ``before_tool_validate``,
    ``before_tool_execute`` and ``after_tool_execute``. Every hook
    no-ops when the capability is disabled (`is_enabled()` is False).
    """

    gate_flag = "g2_enabled"

    @property
    def tool_gate(self) -> G2ToolGate:
        """The underlying `G2ToolGate` (typed view of ``self.gate``)."""
        return self._gate  # type: ignore[return-value]

    def _gate_ctx(self) -> GateContext:
        return GateContext(
            capability_registry=self.sidecar.capability_registry,
            trust_scorer=self.sidecar.trust_scorer,
            quarantine_agent=self.sidecar.quarantine_agent,
            provenance=self.sidecar.provenance,
        )

    # -----------------------------------------------------------------
    # prepare_tools -- allow-list + ETDI (drop offending tools)
    # -----------------------------------------------------------------

    async def prepare_tools(
        self,
        ctx: RunContext,
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        """
        Drop tools that fail the allow-list or the ETDI descriptor-hash
        check, so the model never sees them.

        Allow-list violations are SEV3 (belt-and-suspenders -- the
        agent's toolset filter already enforces the project patterns);
        an ETDI rug-pull (descriptor hash changed mid-session, or differs
        from an operator manifest) is SEV2.
        """
        if not self.is_enabled():
            return tool_defs
        gate = self.tool_gate
        registry = gate.tool_registry
        kept: list[ToolDefinition] = []
        for tool_def in tool_defs:
            if not tool_allowed(tool_def.name, gate.allow_patterns):
                self._record_incident(
                    3,
                    f"G2 allow-list: dropping tool {tool_def.name!r} "
                    f"(not permitted by patterns {gate.allow_patterns})",
                )
                continue
            if registry is not None:
                verification = registry.verify(tool_def.name)
                if not verification.ok:
                    self._record_incident(2, verification.reason)
                    continue
            kept.append(tool_def)
        return kept

    # -----------------------------------------------------------------
    # before_tool_validate -- record the validation attempt
    # -----------------------------------------------------------------

    async def before_tool_validate(
        self,
        ctx: RunContext,
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: Any,
    ) -> Any:
        """
        Record that a tool-arg validation is about to happen, for
        provenance. PydanticAI performs the actual schema check.
        """
        if self.is_enabled():
            logger.debug(
                "VISTAGuard G2: tool %r args entering validation", tool_def.name
            )
        return args

    # -----------------------------------------------------------------
    # before_tool_execute -- taint/high-stakes guard + Minimize
    # -----------------------------------------------------------------

    async def before_tool_execute(
        self,
        ctx: RunContext,
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Run the fast tier (taint walk + high-stakes guard, with
        allow-list/ETDI/schema as a safety net) and, on allow, the
        Minimize slow tier.

        A fast-tier deny raises `SkipToolExecution` (the deny message
        becomes the tool result). When Minimize rewrites the args, the
        rewritten dict is returned -- or, when ``g2_minimize_via_retry``
        is set, a `ModelRetry` is raised to teach the model what was
        stripped.
        """
        if not self.is_enabled() or not isinstance(args, dict):
            return args
        gate = self.tool_gate
        gate_ctx = self._gate_ctx()
        payload = {"tool_name": tool_def.name, "args": args}

        decision = await gate.check_fast(payload, gate_ctx)
        if not decision.allow:
            self._record_incident(decision.incident_level or 2, decision.reason)
            raise SkipToolExecution(
                self._deny_message(tool_def.name, decision.reason)
            )

        # Minimize on inputs (slow tier; no-op when the Q-LLM is absent).
        slow = await gate.check_slow(payload, gate_ctx, decision)
        if slow.rewritten_args is not None:
            if self.settings.g2_minimize_via_retry:
                raise ModelRetry(
                    f"VISTAGuard G2 stripped sensitive content from the "
                    f"arguments to {tool_def.name!r} ({slow.reason}). "
                    f"Re-issue the call without that content."
                )
            logger.info(
                "VISTAGuard G2 Minimize rewrote args for %r: %s",
                tool_def.name, slow.reason,
            )
            return slow.rewritten_args
        return args

    # -----------------------------------------------------------------
    # after_tool_execute -- Sanitize on returns
    # -----------------------------------------------------------------

    async def after_tool_execute(
        self,
        ctx: RunContext,
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        result: Any,
    ) -> Any:
        """
        Sanitize a tool's text return: deny (replace with an error)
        high-confidence injections, strip moderate ones, and tag the
        return in the capability registry so downstream taint walks see
        it.
        """
        if not self.is_enabled() or not isinstance(result, str) or not result:
            return result
        gate = self.tool_gate
        gate_ctx = self._gate_ctx()
        decision = await gate.sanitize_output(
            result, gate_ctx, tool_name=tool_def.name
        )

        if not decision.allow:
            # High-confidence injection: do not forward the original
            # text to the agent; replace it with a structured error.
            self._record_incident(decision.incident_level or 2, decision.reason)
            return self._deny_message(tool_def.name, decision.reason)

        if decision.incident_level is not None:
            self._record_incident(decision.incident_level, decision.reason)

        returned = (
            decision.rewritten_result
            if decision.rewritten_result is not None
            else result
        )
        # Tag the (possibly sanitized) return so a later G2 taint walk
        # over tool arguments inherits its trust state.
        if decision.capability_tag is not None and returned:
            try:
                self.sidecar.capability_registry.tag(returned, decision.capability_tag)
            except Exception as exc:  # noqa: BLE001 -- defensive on bad registry
                logger.warning(
                    "VISTAGuard G2: registry tag write failed (%s: %s)",
                    type(exc).__name__, exc,
                )
        return returned

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _record_incident(self, level: int, reason: str) -> None:
        self.sidecar.incident_manager.record(
            level=level, gate="G2", reason=reason
        )

    @staticmethod
    def _deny_message(tool_name: str, reason: str) -> str:
        return f"VISTAGuard G2 denied call to {tool_name!r}: {reason}"


__all__ = ["G2ToolCapability"]
