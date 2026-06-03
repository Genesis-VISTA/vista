"""
`G4CodeCapability` -- the PydanticAI-hook adapter for the G4 Code Gate.

G4 is the cleanest migration target: the gate (`G4CodeGate`) shipped in
Phase 3 but was never wired into the sidecar's `process_tool_call`, so
it had no production invocation path. This capability provides that path
via hooks and delegates all policy to the existing gate -- the gate's
`check_fast` (Semgrep scan, severity routing, per-tool overrides) and
its 43 unit tests are untouched.

Two hooks:

- ``before_tool_execute`` fires for the code-bearing tools
  (``run_bash`` / ``create_file``). It runs the gate's fast tier on the
  extracted code and routes by severity:

    - ERROR finding -> raise `SkipToolExecution` (the deny message
      becomes the tool result the model sees, so it can recover) and
      record a SEV2 incident.
    - WARNING-only -> record a SEV3 incident and return the args
      unchanged (the call proceeds).
    - clean -> return the args unchanged.

  The Semgrep timeout is bounded here with ``anyio.fail_after`` -- the
  same primitive PydanticAI uses for native hook timeouts -- rather than
  inside ``G4CodeGate._run_semgrep`` (R2 moved it out of the runner). A
  timed-out scan becomes a default-deny SEV2.

- ``prepare_tools`` enforces per-tool rule overrides as a filtering
  step: a tool whose override declaration is malformed is dropped from
  the toolset (Bell-LaPadula default-deny, scoped to that one tool).
  Well-formed overrides keep the tool; the gate excludes the named
  rules at scan time.

## Tool filter

R0's `VistaGuardCapability` base registers hooks by method override
(not the `@hooks.on(...)` decorator), so the ``tools=`` filter is
applied inline: ``before_tool_execute`` returns early for any tool not
in the gate's ``scan_tools`` set (``{"run_bash", "create_file"}`` by
default), which is the same set the decorator's ``tools=`` would pin.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import anyio
from pydantic_ai import RunContext
from pydantic_ai.exceptions import SkipToolExecution

from ..gates.base import GateContext
from .base import VistaGuardCapability

if TYPE_CHECKING:
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolDefinition

    from ..gates.g4_code import G4CodeGate


logger = logging.getLogger(__name__)


class G4CodeCapability(VistaGuardCapability):
    """
    G4 Code Gate exposed as a PydanticAI capability.

    Registers ``before_tool_execute`` (Semgrep scan + severity routing)
    and ``prepare_tools`` (per-tool override filtering). Both no-op when
    the capability is disabled (`is_enabled()` is False).
    """

    gate_flag = "g4_enabled"

    @property
    def code_gate(self) -> G4CodeGate:
        """The underlying `G4CodeGate` (typed view of ``self.gate``)."""
        return self._gate  # type: ignore[return-value]

    # -----------------------------------------------------------------
    # before_tool_execute -- Semgrep scan
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
        Scan code-bearing tool calls; deny ERROR findings, log WARNINGs.
        """
        if not self.is_enabled():
            return args
        tool_name = tool_def.name
        # Tool filter: G4 only inspects code-bearing tools. Anything
        # else passes through untouched (the `tools=` filter the
        # decorator form would pin, applied inline here).
        if tool_name not in self.code_gate.scan_tools:
            return args
        if not isinstance(args, dict):
            # The framework hands validated args as a dict; a non-dict
            # is outside the gate's contract, so leave it for the tool.
            return args

        gate_ctx = GateContext(
            capability_registry=self.sidecar.capability_registry,
            trust_scorer=self.sidecar.trust_scorer,
            contracts=self.sidecar.contracts,
            quarantine_agent=self.sidecar.quarantine_agent,
            provenance=self.sidecar.provenance,
        )
        payload = {"tool_name": tool_name, "args": args}

        timeout = self.code_gate.semgrep_timeout
        try:
            with anyio.fail_after(timeout):
                decision = await self.code_gate.check_fast(payload, gate_ctx)
        except TimeoutError:
            reason = (
                f"G4 semgrep timed out after {timeout}s on "
                f"{tool_name!r}; default-deny"
            )
            self._record_incident(2, reason)
            raise SkipToolExecution(self._deny_message(tool_name, reason))

        if not decision.allow:
            self._record_incident(decision.incident_level or 2, decision.reason)
            raise SkipToolExecution(
                self._deny_message(tool_name, decision.reason)
            )

        # WARNING-only allow path: record SEV3, pass args unchanged.
        if decision.incident_level is not None:
            self._record_incident(decision.incident_level, decision.reason)
        return args

    # -----------------------------------------------------------------
    # prepare_tools -- per-tool override filtering (Bell-LaPadula)
    # -----------------------------------------------------------------

    async def prepare_tools(
        self,
        ctx: RunContext,
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        """
        Drop any code-bearing tool whose per-tool rule override is
        malformed.

        A malformed override is a default-deny condition (Bell-LaPadula).
        Modeling it as a `prepare_tools` filter scopes the deny to that
        single tool -- the model never sees it, so it cannot call it --
        while leaving every other tool available. Well-formed (or
        absent) overrides keep the tool; the gate excludes the named
        rules when it scans.
        """
        if not self.is_enabled():
            return tool_defs
        kept: list[ToolDefinition] = []
        for tool_def in tool_defs:
            if tool_def.name in self.code_gate.scan_tools:
                try:
                    self.code_gate.disabled_rules_for_tool(tool_def.name)
                except ValueError as exc:
                    logger.warning(
                        "VISTAGuard G4: dropping tool %r from the toolset; "
                        "malformed per-tool override (%s)",
                        tool_def.name, exc,
                    )
                    continue
            kept.append(tool_def)
        return kept

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _record_incident(self, level: int, reason: str) -> None:
        self.sidecar.incident_manager.record(
            level=level, gate="G4", reason=reason
        )

    @staticmethod
    def _deny_message(tool_name: str, reason: str) -> str:
        return f"VISTAGuard G4 denied call to {tool_name!r}: {reason}"


__all__ = ["G4CodeCapability"]
