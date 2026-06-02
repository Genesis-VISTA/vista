"""
The 6-configuration **B4-first** cumulative ablation matrix.

The v0.2 work item replaces the old per-gate 3-4-config matrices (and the
earlier 7-config G1-first sweep) with one shared cumulative ablation that
adds gate layers **code-execution-first**, matching the B4-first
plan-of-record:

1. ``baseline``            -- no gates. Control row; every attack lands.
2. ``+G4``                 -- code-execution gate (the first layer).
3. ``+G4+G3``              -- + RAG / retrieval gate.
4. ``+G4+G3+G1``           -- + prompt gate.
5. ``+G4+G3+G1+G5``        -- + HPC-job gate.
6. ``full VISTAGuard``     -- the four gates above **plus** the B2
   enforcement wrapper (G2) and the **trust scorer**.

So the cumulative add order is ``(G4, G3, G1, G5)`` -- four layers across
five steps -- and the final ``full`` config additionally turns on the G2
tool-boundary enforcement wrapper and marks the trust scorer active. G2
is enforcement-only (the five VISTA sub-servers are first-party), so it
is not a separately-ablated red-team layer; it ships inside ``full``.

``build_gate_stack`` turns a config into the concrete, *real* gate
objects (the same classes the production sidecar mounts), each enabled
or not. Gates run fast-tier only unless a quarantine agent is wired (the
harness is offline by default, matching the existing per-gate runners).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..gates.base import Gate
from ..gates.g1_prompt import G1PromptGate
from ..gates.g2_tool import G2ToolGate
from ..gates.g3_rag import G3RagGate
from ..gates.g4_code import G4CodeGate
from ..gates.g5_hpc import G5HpcJobGate


# -----------------------------------------------------------------
# Layers + configs
# -----------------------------------------------------------------


class GateLayer(str, Enum):
    """The gate layers the cumulative ablation can turn on.

    ``G4``/``G3``/``G1``/``G5`` are added one-at-a-time in the B4-first
    cumulative order; ``G2`` (the B2 enforcement wrapper) is enabled only
    in the final ``full`` config alongside the trust scorer.
    """

    G1 = "G1"
    G2 = "G2"
    G3 = "G3"
    G4 = "G4"
    G5 = "G5"


#: The cumulative order the ablation adds layers in -- B4-first.
_LAYER_ORDER: tuple[GateLayer, ...] = (
    GateLayer.G4,
    GateLayer.G3,
    GateLayer.G1,
    GateLayer.G5,
)


@dataclass(frozen=True)
class AblationConfig:
    """One configuration: a name, the live gate layers, and whether the
    trust scorer is active.

    ``trust_active`` is True only in ``full`` -- the trust scorer (and
    its sticky high-stakes caps) is part of full VISTAGuard, not an
    intermediate cumulative layer.
    """

    name: str
    layers: frozenset[GateLayer]
    trust_active: bool = False

    def has(self, layer: GateLayer) -> bool:
        return layer in self.layers

    @property
    def gate_ids(self) -> frozenset[str]:
        """The set of base gate ids live in this config."""
        return frozenset(layer.value for layer in self.layers)


def _cumulative_configs() -> tuple[AblationConfig, ...]:
    """Build the 6 cumulative B4-first configs.

    ``baseline`` then one layer per step in ``_LAYER_ORDER``
    (``+G4`` ... ``+G4+G3+G1+G5``), then ``full`` which adds the G2
    enforcement wrapper and the trust scorer on top.
    """
    configs: list[AblationConfig] = [
        AblationConfig(name="baseline", layers=frozenset())
    ]
    live: set[GateLayer] = set()
    for layer in _LAYER_ORDER:
        live.add(layer)
        name = "+" + "+".join(l.value for l in _LAYER_ORDER if l in live)
        configs.append(AblationConfig(name=name, layers=frozenset(live)))
    # full = the four gates + the B2 enforcement wrapper (G2) + trust.
    configs.append(
        AblationConfig(
            name="full VISTAGuard",
            layers=frozenset(GateLayer),
            trust_active=True,
        )
    )
    return tuple(configs)


#: The pinned 6-config B4-first cumulative ablation matrix.
CUMULATIVE_CONFIGS: tuple[AblationConfig, ...] = _cumulative_configs()


# -----------------------------------------------------------------
# Gate stack
# -----------------------------------------------------------------


class GateStack:
    """The live gates for one configuration, keyed by base gate id.

    The ``SessionRunner`` asks the stack for the gate defending an
    action (``gate_for("G4")``); a gate that is not in this config
    returns None, and the runner records the action as having passed
    with no live defender.
    """

    def __init__(self, gates: dict[str, Gate]) -> None:
        self._gates = gates

    def gate_for(self, gate_id: str | None) -> Gate | None:
        if gate_id is None:
            return None
        return self._gates.get(gate_id)

    def live_ids(self) -> frozenset[str]:
        return frozenset(self._gates.keys())


def build_gate_stack(config: AblationConfig) -> GateStack:
    """Construct the real gate objects for the layers live in ``config``.

    Each gate is the production class with ``enabled=True``. Semgrep
    (G4) and the Q-LLM slow tiers stay off -- the harness runs the
    fast-tier defenses offline, exactly like the existing per-gate eval
    runners. A deployment that wants the full slow tier wires a
    quarantine agent through the ``SessionRunner``.
    """
    gates: dict[str, Gate] = {}
    if config.has(GateLayer.G1):
        gates["G1"] = G1PromptGate(enabled=True)
    if config.has(GateLayer.G2):
        # The B2 enforcement wrapper -- enforcement-only, present in full.
        gates["G2"] = G2ToolGate(enabled=True)
    if config.has(GateLayer.G3):
        # One G3 instance serves the anomaly + hybrid/query-injection
        # tiers; query-injection (the fast tier the harness exercises
        # offline) is on whenever G3 is live.
        gates["G3"] = G3RagGate(enabled=True, query_injection_enabled=True)
    if config.has(GateLayer.G4):
        gates["G4"] = G4CodeGate(enabled=True)
    if config.has(GateLayer.G5):
        gates["G5"] = G5HpcJobGate(enabled=True)
    return GateStack(gates)
