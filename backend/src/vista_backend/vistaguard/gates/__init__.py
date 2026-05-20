"""
VISTAGuard gate implementations.

This package contains the seven boundary-checking gates (G1-G7) and
the `Gate` ABC they share. Each gate is a Phase-1-through-Phase-7
deliverable; Phase 0 ships only the base classes
(`Gate`, `GateDecision`, `GateContext`) plus the `PassThroughGate`
test fixture / sidecar fallback.

Public re-exports from `vista_backend.vistaguard.gates`:

- `Gate` -- the abstract base class with the fast/slow dispatch.
- `GateDecision` -- the frozen result dataclass.
- `GateContext` -- the per-call shared-state dataclass.
- `PassThroughGate` -- a minimal `Gate` implementation that allows
  everything. Used in tests and as the sidecar's disabled-flag
  fallback.

Future re-exports (per-phase): `PromptGate`, `ToolGate`, `RagGate`,
`CodeGate`, `HpcGate`, `InstrumentGate`, `FederationGate`.
"""

from .base import Gate, GateContext, GateDecision, PassThroughGate

__all__ = [
    "Gate",
    "GateContext",
    "GateDecision",
    "PassThroughGate",
]