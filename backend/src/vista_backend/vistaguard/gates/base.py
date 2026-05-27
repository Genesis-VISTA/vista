"""
Gate ABC and decision/context dataclasses for VISTAGuard.

"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..capabilities import CapabilityRegistry, CapabilityTag
from ..trust import TrustScorer


# -----------------------------------------------------------------
# GateDecision
# -----------------------------------------------------------------


@dataclass(frozen=True)
class GateDecision:
    """
    The result of a single gate check.

    """

    allow: bool
    reason: str = ""
    capability_tag: CapabilityTag | None = None
    incident_level: int | None = None
    rewritten_args: dict[str, Any] | None = None
    rewritten_result: str | None = None

    def replace_with(self, **changes: Any) -> "GateDecision":
        """
        Return a new `GateDecision` with the given fields replaced.
        Useful when a slow-tier check wants to refine a fast-tier
        decision without losing fields it doesn't touch.
        """
        from dataclasses import replace
        return replace(self, **changes)


# -----------------------------------------------------------------
# GateContext
# -----------------------------------------------------------------


@dataclass
class GateContext:
    """
    Per-call shared state passed to every gate check.

    """

    capability_registry: CapabilityRegistry
    trust_scorer: TrustScorer
    # Phase-5 field. Will tighten to `ContractLibrary | None` when that
    # class lands.
    contracts: Any | None = None
    # Phase-1 field. Will tighten to `pydantic_ai.Agent | None`.
    quarantine_agent: Any | None = None
    # Phase-0 (separate issue) field. Will tighten to
    # `ProvenanceEmitter | None`.
    provenance: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# -----------------------------------------------------------------
# Gate ABC
# -----------------------------------------------------------------


class Gate(ABC):
    """
    Base class for G1..G7.
    """

    # Subclasses must override.
    name: str = "Gate"

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    # -----------------------------------------------------------------
    # Public, concrete dispatch -- DO NOT OVERRIDE in subclasses
    # -----------------------------------------------------------------

    async def check_fast(self, payload: Any, ctx: GateContext) -> GateDecision:
        """
        Run the fast-tier check.

        When `self.enabled` is False, returns an allow decision
        without invoking subclass logic. 
        """
        if not self.enabled:
            return GateDecision(
                allow=True,
                reason=f"gate {self.name} disabled",
            )
        return await self._check_fast_when_enabled(payload, ctx)

    async def check_slow(
        self,
        payload: Any,
        ctx: GateContext,
        decision: GateDecision,
    ) -> GateDecision:
        """
        Run the slow-tier (Q-LLM-backed) check.

        """
        if not self.enabled:
            return decision
        if ctx.quarantine_agent is None:
            # Slow-tier requested but no Q-LLM available
            return decision
        return await self._check_slow_when_enabled(payload, ctx, decision)

    # -----------------------------------------------------------------
    # Subclass hooks
    # -----------------------------------------------------------------

    @abstractmethod
    async def _check_fast_when_enabled(
        self,
        payload: Any,
        ctx: GateContext,
    ) -> GateDecision:
        """
        Subclass-provided fast-tier logic.
        """

    async def _check_slow_when_enabled(
        self,
        payload: Any,
        ctx: GateContext,
        decision: GateDecision,
    ) -> GateDecision:
        """
        Subclass-provided slow-tier logic.
        """
        return decision


# -----------------------------------------------------------------
# PassThroughGate -- a minimal gate used by tests and by the
# disabled-master-flag path of the sidecar.
# -----------------------------------------------------------------


class PassThroughGate(Gate):
    """
    A trivial `Gate` that always allows.

    """

    name = "PassThrough"

    def __init__(
        self,
        *,
        enabled: bool = True,
        reason: str = "pass-through",
    ) -> None:
        super().__init__(enabled=enabled)
        self._reason = reason

    async def _check_fast_when_enabled(
        self,
        payload: Any,
        ctx: GateContext,
    ) -> GateDecision:
        return GateDecision(allow=True, reason=self._reason)