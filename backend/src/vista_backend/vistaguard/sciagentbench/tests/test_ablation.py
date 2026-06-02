"""
Ablation-matrix tests.

Pins the 6-configuration **B4-first** cumulative shape
(``baseline -> +G4 -> +G4+G3 -> +G4+G3+G1 -> +G4+G3+G1+G5 -> full``) and
that ``build_gate_stack`` produces the real, correctly-enabled gates per
config.
"""

from __future__ import annotations

from ..ablation_matrix import (
    CUMULATIVE_CONFIGS,
    GateLayer,
    build_gate_stack,
)


def test_six_cumulative_configs_b4_first():
    names = [c.name for c in CUMULATIVE_CONFIGS]
    assert names == [
        "baseline",
        "+G4",
        "+G4+G3",
        "+G4+G3+G1",
        "+G4+G3+G1+G5",
        "full VISTAGuard",
    ]
    # baseline has no layers; full has all five and the trust scorer.
    assert CUMULATIVE_CONFIGS[0].layers == frozenset()
    assert CUMULATIVE_CONFIGS[0].trust_active is False
    assert CUMULATIVE_CONFIGS[-1].layers == frozenset(GateLayer)
    assert CUMULATIVE_CONFIGS[-1].trust_active is True


def test_code_gate_added_first():
    # B4-first: the first non-baseline config adds exactly G4.
    assert CUMULATIVE_CONFIGS[1].layers == frozenset({GateLayer.G4})


def test_configs_are_cumulative():
    # Each config's layers are a superset of the previous one's; the four
    # single-layer steps grow by exactly one, and `full` adds the G2
    # enforcement wrapper on top.
    prev = frozenset()
    for config in CUMULATIVE_CONFIGS:
        assert prev <= config.layers
        prev = config.layers


def test_trust_active_only_in_full():
    active = [c.name for c in CUMULATIVE_CONFIGS if c.trust_active]
    assert active == ["full VISTAGuard"]


def test_g2_only_in_full():
    # The B2 enforcement wrapper (G2) appears only in the full config.
    with_g2 = [c.name for c in CUMULATIVE_CONFIGS if GateLayer.G2 in c.layers]
    assert with_g2 == ["full VISTAGuard"]


def test_gate_stack_baseline_has_no_gates():
    stack = build_gate_stack(CUMULATIVE_CONFIGS[0])
    assert stack.live_ids() == frozenset()
    assert stack.gate_for("G4") is None


def test_gate_stack_g4_first_config():
    stack = build_gate_stack(CUMULATIVE_CONFIGS[1])
    assert stack.live_ids() == frozenset({"G4"})
    g4 = stack.gate_for("G4")
    assert g4 is not None and g4.enabled
    # G1/G3/G5 not yet live in the B4-first first step.
    assert stack.gate_for("G1") is None


def test_gate_stack_full_has_all_base_gates():
    stack = build_gate_stack(CUMULATIVE_CONFIGS[-1])
    assert stack.live_ids() == frozenset({"G1", "G2", "G3", "G4", "G5"})
    for gid in ("G1", "G2", "G3", "G4", "G5"):
        gate = stack.gate_for(gid)
        assert gate is not None and gate.enabled
