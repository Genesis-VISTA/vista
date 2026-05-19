afrom abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

@dataclass
class GateDecision:
    allow: bool
    reason: str = ""
    capability_tag: "CapabilityTag | None" = None
    incident_level: int | None = None   # None | 1 | 2 | 3
    rewritten_args: dict[str, Any] | None = None  # for Minimize-and-Sanitize

class Gate(ABC):
    """Base class for G1..G7."""
    name: str
    enabled: bool

    @abstractmethod
    async def check_fast(self, payload: Any, ctx: "GateContext") -> GateDecision: ...

    async def check_slow(self, payload: Any, ctx: "GateContext", decision: GateDecision) -> GateDecision:
        """Optional Q-LLM-backed check. Default: pass through."""
        return decision
