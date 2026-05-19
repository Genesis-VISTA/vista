afrom dataclasses import dataclass, field
from enum import Enum
from typing import Any

class SensitivityTier(str, Enum):
    OPEN = "open"
    INTERNAL = "internal"
    CUI = "cui"
    EXPORT_CONTROLLED = "export_controlled"

class DualUseMarker(str, Enum):
    NONE = "none"
    CHEM = "chem"
    BIO = "bio"
    NUCLEAR = "nuclear"
    CYBER = "cyber"

class TrustTier(str, Enum):
    NORMAL = "normal"
    ELEVATED = "elevated"
    RESTRICTED = "restricted"
    TERMINATED = "terminated"

@dataclass
class CapabilityTag:
    """Provenance and trust tags carried with every value crossing a boundary."""
    source: str                                    # "user" | "tool:<name>" | "rag:<corpus>" | "agent:<id>"
    sensitivity: SensitivityTier = SensitivityTier.OPEN
    dual_use: DualUseMarker = DualUseMarker.NONE
    taint: bool = True                             # default-deny: untrusted until cleared
    provenance_chain: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

class CapabilityRegistry:
    """Per-session map of value-id → CapabilityTag. Lives on ProjectAgent."""
    def __init__(self):
        self._tags: dict[str, CapabilityTag] = {}
    def tag(self, value_id: str, tag: CapabilityTag) -> None: ...
    def get(self, value_id: str) -> CapabilityTag | None: ...
    def propagate(self, source_ids: list[str], target_id: str, sink: str) -> CapabilityTag: ...