"""
Gate ABC and decision/context dataclasses for VISTAGuard.

This module defines the contract every VISTAGuard gate (G1-G7) must
satisfy. Gates are the boundary-checking primitives that fire on
specific events crossing the agent runtime:

- G1 fires on user prompts entering the agent.
- G2 fires on outgoing tool calls and incoming tool returns.
- G3 fires on retrieved RAG chunks.
- G4 fires on code being submitted to the sandbox.
- G5 fires on HPC job submissions.
- G6 fires on instrument commands.
- G7 fires on inter-agent / cross-facility messages.

Each gate has two layers: a *fast* deterministic check that runs
synchronously per event, and an optional *slow* Q-LLM-backed check
that runs only when warranted. The fast/slow split lets a deployment
trade latency against assurance per gate; see main proposal §2.4.

## The Template Method pattern

`Gate.check_fast` and `Gate.check_slow` are concrete public methods
that handle the enabled/disabled dispatch. Subclasses override
`_check_fast_when_enabled` (abstract) and optionally
`_check_slow_when_enabled` (concrete pass-through default) to
provide the actual checking logic. The reason for this split is
that the enabled-flag check is the load-bearing modularity contract
of VISTAGuard: a misconfigured gate that forgot to check `enabled`
before doing its work would silently invoke security logic in
deployments where it's supposed to be off. Putting the dispatch in
the base class makes that impossible: subclasses get the disabled-
path behavior for free and cannot bypass it without explicit override.

The public-method acceptance criterion ("disabled gate's `check_fast`
returns `allow=True`") is satisfied by this pattern: callers invoke
`gate.check_fast(payload, ctx)` and get the disabled-path result
without `_check_fast_when_enabled` ever being called.

## GateContext

`GateContext` is the per-call carrier of shared state -- capability
registry, trust scorer, contract library, quarantine agent,
provenance emitter. Gates read from it (e.g., consulting the
capability registry to check argument taint) and write to it (e.g.,
recording a violation via the trust scorer).

Several `GateContext` fields are typed as `Any | None` in Phase 0
because the classes they hold don't exist yet (contracts arrive in
Phase 5, quarantine in Phase 1, provenance in its own phase-0
issue). Each phase that introduces a real class will tighten the
type annotation here -- that's a narrowing refactor, not a breaking
change, because no Phase-0 gate code calls into these fields yet.
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

    Frozen so that gates can pass decisions up the call stack
    without callers being able to mutate them. A gate that wants
    to amend a previous decision (e.g., a slow-tier check
    refining a fast-tier pass) should return a new
    `GateDecision`; the `replace_with(...)` helper exists for that
    case.

    Fields:
        allow: pass-through (True) or block (False).
        reason: human-readable explanation. Empty by convention
            when allow=True; required by convention when allow=False
            so the operator and audit trail know why.
        capability_tag: a tag to attach to the value flowing
            through the gate (e.g., the capability of a tool
            return). None when the decision doesn't produce or
            modify a capability tag. Set by G2 / G3 / G4 / G5
            after they successfully gate a value.
        incident_level: optional SEV1/2/3 marker. None means no
            incident; 3 = informational, 2 = warning (triggers
            tier elevation in Phase 5), 1 = severe (triggers
            session termination in Phase 5).
        rewritten_args: optional sanitized version of input
            arguments for the Minimize-and-Sanitize pattern (main
            proposal §2.2 G2). When non-None, the sidecar
            substitutes these for the original arguments before
            forwarding to the upstream tool. Typically only set
            by slow-tier (Q-LLM-backed) checks.
    """

    allow: bool
    reason: str = ""
    capability_tag: CapabilityTag | None = None
    incident_level: int | None = None
    rewritten_args: dict[str, Any] | None = None

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

    The capability registry and trust scorer are present from
    Phase 0; the other fields are typed as `Any | None` until the
    phase that introduces them lands (see module docstring).
    Gates may safely assume `capability_registry` and
    `trust_scorer` are non-None; they must defensively handle
    `contracts`, `quarantine_agent`, and `provenance` being None.

    The class is intentionally a plain (non-frozen) dataclass so
    that gates can mutate the shared registries during a check.
    The dataclass equality / hashing is irrelevant -- contexts
    are short-lived per-call objects, not registry keys.

    Fields:
        capability_registry: per-session capability tracking.
        trust_scorer: per-session trust scorer.
        contracts: contract library (Phase 5). Currently `Any | None`.
        quarantine_agent: Q-LLM PydanticAI Agent (Phase 1).
            Currently `Any | None`. Gates should test for None
            before invoking; `check_slow` no-ops when this is None.
        provenance: provenance emitter (Phase-0 issue 6).
            Currently `Any | None`.
        metadata: free-form per-call annotations a gate may set
            for downstream gates in the same call. Examples
            include G1's extracted intent (consumed later by G4)
            and G3's per-chunk capability tags.
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

    Subclasses must:

    - Set the class attribute `name` to a short identifier
      (e.g., `"G2"`) used in logs, the trust scorer's
      `capability_kind`, and provenance records.
    - Implement `_check_fast_when_enabled(payload, ctx) ->
      GateDecision`. This is the gate's actual fast-tier logic.

    Subclasses may optionally:

    - Override `_check_slow_when_enabled(payload, ctx, decision)
      -> GateDecision` to add Q-LLM-backed semantic checks. The
      default implementation returns the input decision
      unchanged.

    The public `check_fast(payload, ctx)` and
    `check_slow(payload, ctx, decision)` methods are concrete on
    the base class and handle the enabled/disabled dispatch. Do
    not override them in subclasses; override the
    `_*_when_enabled` hooks instead. This contract is what makes
    `enabled=False` a structural no-op rather than a per-gate
    discipline.

    Concrete gates conventionally accept their enabled flag from
    `VistaGuardSettings` via the sidecar; the base class accepts
    it as a constructor argument so test fixtures can construct
    gates without dragging in settings.
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
        without invoking subclass logic. This is the load-bearing
        modularity contract; subclasses cannot bypass it because
        the abstract method lives on `_check_fast_when_enabled`,
        not on `check_fast` itself.
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

        When `self.enabled` is False or `ctx.quarantine_agent` is
        None, returns the input `decision` unchanged. The
        quarantine-agent guard means gates can safely call
        `check_slow` without checking whether slow-tier is
        enabled at the sidecar level.

        Subclasses with no slow-tier behavior inherit the default
        pass-through implementation and need not override.
        """
        if not self.enabled:
            return decision
        if ctx.quarantine_agent is None:
            # Slow-tier requested but no Q-LLM available -- pass
            # through. This matches the deployment-time pattern
            # where `quarantine_enabled=False` leaves the agent
            # absent and gates run fast-only.
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

        Invoked by `check_fast` only when `self.enabled` is True.
        Implementations should return a `GateDecision` describing
        whether to allow, why, and any side effects (capability
        tag, incident level, rewritten arguments).
        """

    async def _check_slow_when_enabled(
        self,
        payload: Any,
        ctx: GateContext,
        decision: GateDecision,
    ) -> GateDecision:
        """
        Subclass-provided slow-tier logic.

        Default: pass through (`return decision`). Override to add
        Q-LLM-backed semantic checks. Invoked by `check_slow` only
        when `self.enabled` is True AND `ctx.quarantine_agent` is
        non-None.
        """
        return decision


# -----------------------------------------------------------------
# PassThroughGate -- a minimal gate used by tests and by the
# disabled-master-flag path of the sidecar.
# -----------------------------------------------------------------


class PassThroughGate(Gate):
    """
    A trivial `Gate` that always allows.

    Used in two places:

    - Tests, to exercise the base-class machinery without a real
      gate's policy logic.
    - The sidecar's disabled-flag fallback (Phase-0 issue 7), as
      a placeholder when the master flag is on but a specific
      gate is disabled.

    The `name` attribute is `"PassThrough"` so logs and
    provenance records distinguish it from real G1..G7 gates. The
    optional `reason` constructor argument lets callers
    distinguish multiple pass-through instances in logs.
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