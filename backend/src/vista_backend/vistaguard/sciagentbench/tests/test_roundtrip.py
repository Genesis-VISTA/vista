"""
End-to-end round-trip test -- the work item's headline acceptance
criterion:

  "One synthetic B1 direct-injection instance round-trips end-to-end
   (loader -> SessionRunner -> trace recorder -> scorer)."

We load the bundled B1.1 instance off disk, run it under the baseline
(no gates) and the first G1-live config (``+G4+G3+G1`` in the B4-first
ablation), and assert the scorer reports a successful attack at baseline
and a blocked attack once G1 is live -- i.e. every stage of the pipeline
produced a usable artifact and the ASR moves the way the boundary -> gate
mapping says it should. The companion benign instance must run clean
through the ``full`` config (the §B false-positive set wiring).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio

from ..ablation_matrix import CUMULATIVE_CONFIGS, GateLayer
from ..instance_loader import bundled_instances_dir, load_instance, load_instances
from ..schemas import Instance
from ..scorer import score_trace
from ..session_runner import SessionRunner
from ..trace_recorder import Trace
from ..turn_buffer import InProcessTurnBuffer


def _baseline_config():
    return next(c for c in CUMULATIVE_CONFIGS if not c.layers)


def _g1_live_config():
    # The first cumulative config that has added G1 (B4-first: +G4+G3+G1).
    return next(c for c in CUMULATIVE_CONFIGS if GateLayer.G1 in c.layers)


def _full_config():
    return CUMULATIVE_CONFIGS[-1]


def _load_b11() -> Instance:
    path = bundled_instances_dir() / "b1_1_direct_injection_example.yaml"
    return load_instance(path)


def _load_benign() -> Instance:
    path = bundled_instances_dir() / "b1_1_benign_companion.yaml"
    return load_instance(path)


async def test_b11_roundtrips_loader_to_scorer():
    instance = _load_b11()
    assert instance.boundary == "B1.1"
    assert instance.is_attack

    runner = SessionRunner(memory_store=InProcessTurnBuffer())

    # Baseline: no gates -> the injection prompt is allowed -> attack
    # succeeds.
    baseline_trace = await runner.run(instance, _baseline_config())
    assert isinstance(baseline_trace, Trace)
    assert baseline_trace.actions, "trace recorder produced no action records"
    baseline_score = score_trace(baseline_trace, instance)
    assert baseline_score.attack_success is True

    # +G4+G3+G1: the G1 fast-tier regex catches the instruction-override /
    # system-prompt-exfil string -> attack blocked.
    g1_trace = await runner.run(instance, _g1_live_config())
    g1_score = score_trace(g1_trace, instance)
    assert g1_score.attack_success is False

    # Utility (the legitimate salt query) survives in both configs: UA
    # holds, so the gate didn't break the real task.
    assert baseline_score.utility_success is True
    assert g1_score.utility_success is True


async def test_g1_blocks_attack_action_only():
    """The G1-live trace blocks the injection action but allows the benign one."""
    instance = _load_b11()
    runner = SessionRunner()
    trace = await runner.run(instance, _g1_live_config())

    attack_records = [a for a in trace.actions if a.is_attack]
    utility_records = [a for a in trace.actions if a.is_utility]
    assert attack_records and all(not a.allowed for a in attack_records)
    assert utility_records and all(a.allowed for a in utility_records)
    # The blocking gate is recorded as G1.
    assert all(a.blocked_by == "G1" for a in attack_records)


async def test_benign_companion_clean_through_full():
    """A §B benign prompt runs clean through the full config (FP-set wiring)."""
    instance = _load_benign()
    assert not instance.is_attack
    runner = SessionRunner()
    trace = await runner.run(instance, _full_config())
    assert trace.actions and all(a.allowed for a in trace.actions)
    score = score_trace(trace, instance)
    assert score.benign_success is True


async def test_bundled_instances_all_load():
    """Both bundled fixtures load and validate against the schema."""
    instances = load_instances(bundled_instances_dir())
    ids = {i.instance_id for i in instances}
    assert "b1_1_direct_injection_example" in ids
    assert "b1_1_benign_companion" in ids
    # One attack, one benign.
    assert sum(1 for i in instances if i.is_attack) == 1
    assert sum(1 for i in instances if not i.is_attack) == 1
