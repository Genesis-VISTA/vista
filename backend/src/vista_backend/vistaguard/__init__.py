"""
VISTAGuard -- security and safety mediation sidecar for VISTA's agent
runtime.

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