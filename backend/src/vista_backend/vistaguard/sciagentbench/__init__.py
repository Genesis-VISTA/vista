"""
SciAgentBench evaluation harness (VISTAGuard Phase 9).

This package is the *runnable shape* every later SciAgentBench
template (Phase 10), correctness-contract test (Phase 11), and RL
policy (Phase 13+) plugs into. It delivers nothing template-specific
on its own -- Phase 9's job is the substrate:

- ``schemas`` -- frozen instance / trace / scorer dataclasses plus
  the JSON-Schema files they serialize to. Frozen at the end of
  week 25 so Phase 10 authoring doesn't churn the format.
- ``instance_loader`` -- load instance YAML/JSON off disk into
  validated ``Instance`` objects.
- ``turn_buffer`` -- the in-process turn buffer threading values across
  the sessions of one episode. There is **no** persistent cross-session
  memory store on the default path; the durable SQLite/Redis backends
  (and the MINJA/MemoryGraft sequences) are deferred to CHUNKS under
  ``sciagentbench/deferred/``.
- ``session_runner`` -- the ``SessionRunner`` that threads a
  deterministic session-id through the turn buffer and runs each
  session's turns through the *real* VISTAGuard gate stack. Extends
  the single-turn ``ProjectAgent.run_stream`` into a multi-session
  episode.
- ``trace_recorder`` -- records per-action gate decisions and
  capability-registry snapshots into a ``Trace`` the scorer (and the
  Phase-13 RL reward) consume.
- ``scorer`` -- the BU / UA / ASR scorer: a per-class programmatic
  check with an LLM-judge fallback, emitting per-(boundary, attack-class)
  cells.
- ``ablation_matrix`` -- the 6-configuration B4-first cumulative ablation
  (baseline -> +G4 -> +G4+G3 -> +G4+G3+G1 -> +G4+G3+G1+G5 ->
  full VISTAGuard).

The shared CLI lives at
``vista_backend.vistaguard.eval.sciagentbench_runner`` and is reachable
as ``python -m vista_backend.vistaguard.eval --gate sciagentbench``.
"""

from .ablation_matrix import (
    CUMULATIVE_CONFIGS,
    AblationConfig,
    GateLayer,
    GateStack,
    build_gate_stack,
)
from .instance_loader import (
    bundled_instances_dir,
    load_instance,
    load_instances,
)
from .schemas import (
    Action,
    ActionKind,
    Instance,
    Session,
    SuccessCriterion,
    Turn,
)
from .scorer import (
    METRIC_DEFINITIONS,
    Cell,
    InstanceScore,
    aggregate_cells,
    score_trace,
)
from .session_runner import SessionRunner
from .trace_recorder import (
    ActionRecord,
    Trace,
    TraceRecorder,
)
from .turn_buffer import (
    InProcessTurnBuffer,
    MemoryRecord,
    MemoryStore,
)

__all__ = [
    "AblationConfig",
    "Action",
    "ActionKind",
    "ActionRecord",
    "CUMULATIVE_CONFIGS",
    "Cell",
    "GateLayer",
    "GateStack",
    "InProcessTurnBuffer",
    "Instance",
    "InstanceScore",
    "METRIC_DEFINITIONS",
    "MemoryRecord",
    "MemoryStore",
    "Session",
    "SessionRunner",
    "SuccessCriterion",
    "Trace",
    "TraceRecorder",
    "Turn",
    "aggregate_cells",
    "build_gate_stack",
    "bundled_instances_dir",
    "load_instance",
    "load_instances",
    "score_trace",
]
