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
        # The Q-LLM slow tier runs only when a quarantine agent is wired;
        # the contract-registry check needs no model, so it runs whenever
        # the gate is enabled (Phase 5).
        if ctx.quarantine_agent is not None:
            decision = await self._check_slow_when_enabled(payload, ctx, decision)
        return self._apply_contract_checks(payload, ctx, decision)

    # -----------------------------------------------------------------
    # Slow-tier contract enforcement (Phase 5)
    # -----------------------------------------------------------------

    #: Claim types this gate's slow tier never needs to look past (unused
    #: today; the registry routes by claim ``type``). Kept so a future
    #: gate can scope its contract checks if needed.
    contract_domains: tuple[str, ...] = ()

    def _extract_contract_claims(self, payload: Any, ctx: GateContext) -> list[dict]:
        """Claims this gate's slow tier should run through the registry.

        Default: a ``claims`` list on a dict payload, plus any on
        ``ctx.metadata['claims']``. Gates whose claims live elsewhere
        (e.g. inside retrieved text) override this.
        """
        claims: list[dict] = []
        if isinstance(payload, dict) and isinstance(payload.get("claims"), list):
            claims.extend(c for c in payload["claims"] if isinstance(c, dict))
        meta = getattr(ctx, "metadata", None) or {}
        if isinstance(meta.get("claims"), list):
            claims.extend(c for c in meta["claims"] if isinstance(c, dict))
        return claims

    def _apply_contract_checks(
        self, payload: Any, ctx: GateContext, decision: GateDecision
    ) -> GateDecision:
        """Run the contract registry over the gate's claims and fold any
        violation into `decision` (deny + bumped incident level)."""
        registry = getattr(ctx, "contracts", None)
        if registry is None:
            return decision
        claims = self._extract_contract_claims(payload, ctx)
        if not claims:
            return decision
        from ..contracts.enforcement import enforce

        outcome = enforce(registry, claims, ctx.trust_scorer)
        if outcome.ok:
            return decision
        level = outcome.incident_level or 2
        bumped = level if decision.incident_level is None else min(
            decision.incident_level, level
        )
        prefix = f"{decision.reason}; " if decision.reason else ""
        return decision.replace_with(
            allow=False,
            incident_level=bumped,
            reason=f"{prefix}{self.name} contract violation -> {outcome.reason}",
        )

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