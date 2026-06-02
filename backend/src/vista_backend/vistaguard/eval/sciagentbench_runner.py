"""
SciAgentBench evaluation runner (the shared ``--gate sciagentbench`` CLI
backend).

Threads every loaded instance through the 6-configuration B4-first
cumulative ablation matrix using the Phase-9 ``SessionRunner`` + scorer,
and returns a ``SciAgentBenchResult`` the report formatter turns into the
per-(boundary, template) cell tables.

Unlike the per-gate runners, this one is *corpus-driven*: it loads
instance documents off disk (the bundled Phase-9 fixtures by default, or
a directory of Phase-10 instances) rather than generating scenarios in
code. That's the whole point of Phase 9 -- one harness shape every later
template plugs instances into.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..sciagentbench import (
    CUMULATIVE_CONFIGS,
    AblationConfig,
    Cell,
    InProcessTurnBuffer,
    InstanceScore,
    SessionRunner,
    aggregate_cells,
    load_instances,
    score_trace,
)
from ..sciagentbench.corpus_builder import CORPUS_DIR
from ..sciagentbench.scorer import METRIC_DEFINITIONS


@dataclass(frozen=True)
class SciAgentBenchResult:
    """Top-level result for the report formatter.

    Fields:
        configs: the ablation configurations measured (the 6 cumulative
            B4-first ones by default).
        cells: per-(boundary, template, config) aggregated cells.
        scores: every per-instance score (kept so a caller can compute
            extra breakdowns, e.g. hard-win audits).
        n_instances / n_attack / n_benign: corpus bookkeeping.
        seed: forwarded for report provenance (the harness itself is
            deterministic; the seed is recorded for parity with the
            other eval reports).
        metric_definitions: the pinned BU / UA / ASR definitions.
    """

    configs: tuple[AblationConfig, ...]
    cells: tuple[Cell, ...]
    scores: tuple[InstanceScore, ...]
    n_instances: int
    n_attack: int
    n_benign: int
    seed: int
    metric_definitions: dict[str, str]


async def run_sciagentbench_evaluation(
    *,
    instances_dir: str | Path | None = None,
    seed: int = 42,
    configs: tuple[AblationConfig, ...] = CUMULATIVE_CONFIGS,
) -> SciAgentBenchResult:
    """Run the full SciAgentBench ablation over the loaded instances.

    Args:
        instances_dir: directory of instance YAML/JSON files. None loads
            the committed active attack corpus.
        seed: recorded for report provenance.
        configs: the ablation matrix. Defaults to the 6 cumulative
            B4-first configs.

    Returns:
        A ``SciAgentBenchResult``.
    """
    instances = load_instances(instances_dir if instances_dir is not None else CORPUS_DIR)

    scores: list[InstanceScore] = []
    # Outer loop over configs, inner over instances, so cells group
    # cleanly per config in the cumulative order. Each instance gets a
    # fresh in-process turn buffer (no persistent cross-session store).
    for config in configs:
        for instance in instances:
            runner = SessionRunner(memory_store=InProcessTurnBuffer())
            trace = await runner.run(instance, config)
            scores.append(score_trace(trace, instance))

    # Re-group so cells iterate (boundary, template) with all configs
    # adjacent -- the report wants one row block per (boundary, template).
    scores_by_cell_order = _reorder_for_report(scores, configs)
    cells = aggregate_cells(scores_by_cell_order)

    n_attack = sum(1 for i in instances if i.is_attack)
    return SciAgentBenchResult(
        configs=tuple(configs),
        cells=tuple(cells),
        scores=tuple(scores),
        n_instances=len(instances),
        n_attack=n_attack,
        n_benign=len(instances) - n_attack,
        seed=seed,
        metric_definitions=dict(METRIC_DEFINITIONS),
    )


def _reorder_for_report(
    scores: list[InstanceScore], configs: tuple[AblationConfig, ...]
) -> list[InstanceScore]:
    """Order scores so all configs of one (boundary, template) are adjacent.

    ``aggregate_cells`` preserves first-seen order; emitting
    (boundary, template) blocks with configs in cumulative order makes
    the report read as "watch ASR fall as gates are added."
    """
    config_rank = {c.name: i for i, c in enumerate(configs)}

    def key(s: InstanceScore) -> tuple[str, str, int]:
        return (s.boundary, s.template, config_rank.get(s.config_name, 0))

    return sorted(scores, key=key)
