"""
VISTAGuard -- security and safety mediation sidecar for VISTA's agent
runtime.

This package is the implementation of the VISTAGuard architecture
described in the project's design documents. It runs as a sidecar to
`ProjectAgent` and gates the seven boundaries an agentic scientific
system crosses: user prompt (G1), MCP tool calls (G2), RAG retrieval
(G3), generated code (G4), HPC job submission (G5), scientific
instrument commands (G6), and inter-agent / cross-facility messages
(G7).

The package is gated behind environment-variable toggles. With the
master flag off (`VISTA_BACKEND_VISTAGUARD__ENABLED=false`, the
default), the runtime hooks are byte-identical no-ops; baseline VISTA
behavior is unchanged.

Public surface (re-exported here for convenience):

- `VistaGuardSettings` -- the configuration model mounted on
  `vista_backend.config.Settings.vistaguard`.
- `CapabilityTag`, `CapabilityRegistry` -- per-session capability
  tracking.
- `SensitivityTier`, `DualUseMarker`, `TrustTier` -- enums used by
  capability tags and the trust scorer.
- `TrustScorer`, `TierPolicy` -- per-session trust scoring. Phase
  0 ships the API; Phase 5 fills in the Bayesian internals.
- `Gate`, `GateDecision`, `GateContext`, `PassThroughGate` -- gate
  ABC and supporting dataclasses. Concrete G1-G7 gates land in
  later phases.
- `IncidentManager`, `IncidentRecord` -- SEV1/SEV2/SEV3 routing
  to logging, trust scorer, and provenance.
- `ProvenanceEmitter`, `ProvenanceEvent` -- the audit-substrate
  adapter. Phase 0 writes JSONL; Phase 7 adds Flowcept-broker
  emission.
- `VistaGuardSidecar` -- the per-`ProjectAgent` composite that
  owns the registry / scorer / incident manager / provenance
  emitter and exposes the `process_tool_call` hook. Phase 0
  ships a pass-through shell; gates land in Phase 1+.
"""

from .capabilities import (
    CapabilityRegistry,
    CapabilityTag,
    DualUseMarker,
    SensitivityTier,
    TrustTier,
)
from .config import VistaGuardSettings
from .gates import Gate, GateContext, GateDecision, PassThroughGate
from .incidents import IncidentManager, IncidentRecord
from .provenance import ProvenanceEmitter, ProvenanceEvent
from .sidecar import VistaGuardSidecar
from .trust import TierPolicy, TrustScorer

__all__ = [
    "CapabilityRegistry",
    "CapabilityTag",
    "DualUseMarker",
    "Gate",
    "GateContext",
    "GateDecision",
    "IncidentManager",
    "IncidentRecord",
    "PassThroughGate",
    "ProvenanceEmitter",
    "ProvenanceEvent",
    "SensitivityTier",
    "TierPolicy",
    "TrustScorer",
    "TrustTier",
    "VistaGuardSettings",
    "VistaGuardSidecar",
]