"""
Tests for PALISADE gate timing (evaluation plan M4): the `Gate`
base-class dispatch emits per-tier `gate.<name>.<tier>` metric events at
`perf` level (or with the GATE_TIMING override), stays silent otherwise,
and never changes the decision either way. Also covers the optional
`duration_ms` on `ProvenanceEmitter.emit_gate_decision`.
"""

import json
from pathlib import Path

import pytest

import palisade.instrumentation as palisade_instrumentation
import vista_backend.metrics as metrics_module
from vista_backend.metrics import MetricsRecorder, MetricsSettings
from palisade.config import PalisadeSettings
from palisade.gates.base import (
    GateContext,
    GateDecision,
    PassThroughGate,
)
from palisade.provenance import ProvenanceEmitter


def make_recorder(
    tmp_path: Path, level: str = "perf", **kwargs
) -> tuple[MetricsRecorder, Path]:
    log_path = tmp_path / "metrics.jsonl"
    return MetricsRecorder(
        MetricsSettings(level=level, **kwargs), default_log_path=log_path
    ), log_path


def read_events(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines()]


@pytest.fixture
def ctx() -> GateContext:
    # GateContext is a plain dataclass; PassThroughGate's tiers never touch
    # the registry or scorer, so bare None stand-ins suffice.
    return GateContext(capability_registry=None, trust_scorer=None)


# ---------------------------------------------------------------------------
# Recorder probe gating
# ---------------------------------------------------------------------------


def test_gate_probe_is_perf_level(tmp_path):
    rec_prod, log_prod = make_recorder(tmp_path / "prod", level="prod", sample_rate=1.0)
    assert rec_prod.gate(gate="G2", tier="fast", duration_ms=1.0, allow=True) is None
    assert not log_prod.exists()

    rec_perf, log_perf = make_recorder(tmp_path / "perf", level="perf")
    assert (
        rec_perf.gate(gate="G2", tier="fast", duration_ms=1.0, allow=False) is not None
    )
    (event,) = read_events(log_perf)
    assert event["event_type"] == "gate.G2.fast"
    assert event["status"] == "deny"


def test_gate_probe_override_at_prod(tmp_path):
    rec, log_path = make_recorder(
        tmp_path, level="prod", sample_rate=1.0, gate_timing="on"
    )
    assert rec.gate(gate="G5", tier="slow", duration_ms=2.0, allow=True) is not None
    assert read_events(log_path)[0]["event_type"] == "gate.G5.slow"


# ---------------------------------------------------------------------------
# Gate base-class dispatch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_fast_emits_timing(tmp_path, monkeypatch, ctx):
    rec, log_path = make_recorder(tmp_path)
    monkeypatch.setattr(metrics_module, "_recorder", rec)
    # PALISADE is a package and cannot import this host: the gate probe is
    # injected, exactly as `ProjectAgent.__init__` does in production.
    monkeypatch.setattr(palisade_instrumentation, "_recorder", rec)

    decision = await PassThroughGate().check_fast({"x": 1}, ctx)
    assert decision.allow is True

    (event,) = read_events(log_path)
    assert event["event_type"] == "gate.PassThrough.fast"
    assert event["status"] == "ok"
    assert event["duration_ms"] >= 0.0


@pytest.mark.anyio
async def test_check_slow_emits_timing(tmp_path, monkeypatch, ctx):
    """The slow tier is timed even with no quarantine agent wired — the
    contract-registry path still runs and is part of the gate's cost."""
    rec, log_path = make_recorder(tmp_path)
    monkeypatch.setattr(metrics_module, "_recorder", rec)
    # PALISADE is a package and cannot import this host: the gate probe is
    # injected, exactly as `ProjectAgent.__init__` does in production.
    monkeypatch.setattr(palisade_instrumentation, "_recorder", rec)

    fast = GateDecision(allow=True, reason="fast ok")
    decision = await PassThroughGate().check_slow({"x": 1}, ctx, fast)
    assert decision == fast

    (event,) = read_events(log_path)
    assert event["event_type"] == "gate.PassThrough.slow"
    assert event["status"] == "ok"


@pytest.mark.anyio
async def test_disabled_gate_emits_nothing(tmp_path, monkeypatch, ctx):
    rec, log_path = make_recorder(tmp_path)
    monkeypatch.setattr(metrics_module, "_recorder", rec)
    # PALISADE is a package and cannot import this host: the gate probe is
    # injected, exactly as `ProjectAgent.__init__` does in production.
    monkeypatch.setattr(palisade_instrumentation, "_recorder", rec)

    gate = PassThroughGate(enabled=False)
    decision = await gate.check_fast({"x": 1}, ctx)
    assert decision.allow and "disabled" in decision.reason
    assert await gate.check_slow({"x": 1}, ctx, decision) == decision
    assert not log_path.exists()


@pytest.mark.anyio
async def test_metrics_off_changes_nothing(tmp_path, monkeypatch, ctx):
    rec, log_path = make_recorder(tmp_path, level="off")
    monkeypatch.setattr(metrics_module, "_recorder", rec)
    # PALISADE is a package and cannot import this host: the gate probe is
    # injected, exactly as `ProjectAgent.__init__` does in production.
    monkeypatch.setattr(palisade_instrumentation, "_recorder", rec)

    gate = PassThroughGate()
    fast = await gate.check_fast({"x": 1}, ctx)
    slow = await gate.check_slow({"x": 1}, ctx, fast)
    assert fast.allow is True and slow == fast
    assert not log_path.exists()


# ---------------------------------------------------------------------------
# Provenance: emit_gate_decision(duration_ms=...)
# ---------------------------------------------------------------------------


def test_emit_gate_decision_carries_duration(tmp_path):
    log_path = tmp_path / "provenance.jsonl"
    emitter = ProvenanceEmitter(
        PalisadeSettings(enabled=True), session_id="s", log_path=log_path
    )
    emitter.emit_gate_decision("G2", GateDecision(allow=True), duration_ms=1.25)
    emitter.emit_gate_decision("G3", GateDecision(allow=False, reason="deny"))

    with_duration, without = [
        json.loads(line) for line in log_path.read_text().splitlines()
    ]
    assert with_duration["duration_ms"] == 1.25
    assert "duration_ms" not in without
