"""
`G3RagCapability` -- the PydanticAI-hook adapter for the G3 RAG/Memory
Gate.

This replaces the bespoke ``VistaGuardSidecar._dispatch_rag_search``
(~80 lines) with two ``rag_search``-only hooks that delegate to the
existing `G3RagGate` and its helper functions:

- ``before_tool_execute`` (fast tier): runs the corpus allow-list /
  sensitivity-tier / query-injection / manifest-hash checks via
  ``G3RagGate.check_fast``. On a deny it raises `SkipToolExecution`
  with an ``ERROR: ...`` string (the same shape ``rag_mcp`` uses for
  its own errors, so the agent can react). On allow it injects
  ``hybrid=True`` + ``alpha`` into the outgoing args when
  ``g3_hybrid_retrieval`` is set -- the hybrid-retrieval injection now
  lives in the hook, not the sidecar.

- ``after_tool_execute`` (slow tier + tagging): when the Q-LLM is
  available, scans each retrieved chunk with
  ``G3RagGate.sanitize_chunks`` and returns the reassembled, sanitized
  text. Otherwise (or when sanitize didn't rewrite) it tags every chunk
  in the capability registry via ``tag_rag_chunks`` so a downstream G2
  taint walk inherits the chunk's trust state.

Both hooks apply only to ``rag_search`` (G3 owns the
``rag_search``-only surface; G2 owns every-tool hooks). The
per-chunk tags are written to the sidecar's capability registry --
the same registry a Phase-5 ``RunContext.deps`` threading would expose
as ``ctx.deps.capability_registry``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunContext
from pydantic_ai.exceptions import SkipToolExecution

from ..contracts import enforce, extract_claims
from ..gates.base import GateContext
from ..gates.g3_hybrid import inject_hybrid_args, should_inject_hybrid
from ..gates.g3_rag import tag_rag_chunks
from .base import VistaGuardCapability

if TYPE_CHECKING:
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolDefinition

    from ..gates.g3_rag import G3RagGate


logger = logging.getLogger(__name__)


class G3RagCapability(VistaGuardCapability):
    """
    G3 RAG/Memory Gate exposed as a PydanticAI capability.
    """

    gate_flag = "g3_enabled"

    #: G3 hooks only fire for this tool (the table assigns the
    #: rag_search-only surface to G3; G2 owns every-tool hooks).
    RAG_TOOL = "rag_search"

    @property
    def rag_gate(self) -> G3RagGate:
        """The underlying `G3RagGate` (typed view of ``self.gate``)."""
        return self._gate  # type: ignore[return-value]

    def _gate_ctx(self) -> GateContext:
        return GateContext(
            capability_registry=self.sidecar.capability_registry,
            trust_scorer=self.sidecar.trust_scorer,
            contracts=self.sidecar.contracts,
            quarantine_agent=self.sidecar.quarantine_agent,
            provenance=self.sidecar.provenance,
        )

    # -----------------------------------------------------------------
    # before_tool_execute -- fast tier + hybrid injection
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
        Gate the outgoing ``rag_search`` call and inject hybrid args.
        """
        if (
            not self.is_enabled()
            or tool_def.name != self.RAG_TOOL
            or not isinstance(args, dict)
        ):
            return args

        gate = self.rag_gate
        kb_slug = args.get("kb_slug")
        query = args.get("query")
        if isinstance(kb_slug, str) and isinstance(query, str):
            decision = await gate.check_fast(
                {"kb_slug": kb_slug, "query": query}, self._gate_ctx()
            )
            if not decision.allow:
                self._record_incident(decision.incident_level or 2, decision.reason)
                logger.warning(
                    "VISTAGuard G3: rag_search denied (kb_slug=%r): %s",
                    kb_slug, decision.reason,
                )
                # Surface the denial as the tool result (same `ERROR: `
                # shape rag_mcp uses), so the agent can recover.
                raise SkipToolExecution(f"ERROR: {decision.reason}")

        # Hybrid retrieval injection lives in the hook (AC2).
        if should_inject_hybrid(self.settings, self.RAG_TOOL):
            return inject_hybrid_args(args, alpha=self.settings.g3_hybrid_alpha)
        return args

    # -----------------------------------------------------------------
    # after_tool_execute -- slow tier + per-chunk tagging
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
        Sanitize retrieved chunks (slow tier) and tag them in the
        registry.
        """
        if (
            not self.is_enabled()
            or tool_def.name != self.RAG_TOOL
            or not isinstance(result, str)
        ):
            return result

        gate = self.rag_gate
        registry = self.sidecar.capability_registry
        kb_slug_raw = args.get("kb_slug") if isinstance(args, dict) else None
        kb_slug = str(kb_slug_raw) if kb_slug_raw else "unknown"

        # Phase-5 slow-tier contract check: run the registry over any
        # structured claims smuggled in the retrieved text (e.g. a
        # poisoned chunk asserting an out-of-bounds property). A violation
        # is recorded as an incident, which feeds the trust scorer.
        self._check_retrieved_claims(result)

        slow_tier_active = (
            self.settings.quarantine_enabled
            and self.sidecar.quarantine_agent is not None
        )
        if slow_tier_active:
            try:
                slow = await gate.sanitize_chunks(
                    result, self._gate_ctx(), kb_slug=kb_slug
                )
            except Exception as exc:  # noqa: BLE001 -- slow tier must not crash
                logger.warning(
                    "VISTAGuard G3 slow-tier failed (%s: %s); "
                    "falling back to fast-tier tag-only path",
                    type(exc).__name__, exc,
                )
            else:
                if slow.rewritten_result is not None:
                    # sanitize_chunks tags the chunks itself; nothing
                    # more to do.
                    return slow.rewritten_result

        try:
            tag_rag_chunks(result, kb_slug=kb_slug, registry=registry)
        except Exception as exc:  # noqa: BLE001 -- tagging must not crash
            logger.warning(
                "VISTAGuard G3: chunk tagging failed (%s: %s); "
                "downstream taint-propagation may be incomplete",
                type(exc).__name__, exc,
            )
        return result

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _check_retrieved_claims(self, result: str) -> None:
        """Run the contract registry over claims found in retrieved text;
        record an incident on any violation (proposal C3)."""
        registry = self.sidecar.contracts
        if registry is None:
            return
        claims = extract_claims(result)
        if not claims:
            return
        outcome = enforce(registry, claims, self.sidecar.trust_scorer)
        if not outcome.ok:
            level = outcome.incident_level or 2
            self._record_incident(
                level, f"G3 contract violation -> {outcome.reason}"
            )

    def _record_incident(self, level: int, reason: str) -> None:
        self.sidecar.incident_manager.record(
            level=level, gate="G3", reason=reason
        )


__all__ = ["G3RagCapability"]
