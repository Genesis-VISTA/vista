#!/usr/bin/env python3
"""
ViT-NAS candidate scorer.

The planner runs this in the sandbox over a cycle's collected results:

    python3 score_candidates.py results.json     # or pipe JSON on stdin

Input JSON: {"efficiency_target": 185,
             "candidates": [{"params": {...}, "metrics": {...}}, ...]}

Metric keys produced by the `vit-train` subagent (one training run per candidate):
    val_loss              — validation loss (lower is better)     [from "Avg val loss="]
    throughput_samples_s  — training throughput, samples/sec      [from "avg N samples/sec"]

Objective (matches AgentHPC ClimateViT): maximize a single efficiency score that rewards
being fast AND accurate at once:

    efficiency = throughput_samples_s / val_loss

A candidate missing either metric, or with a non-positive val_loss, can't be scored and is
reported as infeasible (never ranked). Feasible candidates are ranked by efficiency,
highest first; the target is met when the best efficiency reaches `efficiency_target`.
"""
from __future__ import annotations

import json
import sys


DEFAULT_EFFICIENCY_TARGET = 185.0


def efficiency_failure(metrics: dict) -> str | None:
    """Return a reason string if the efficiency score can't be computed, else None."""
    val_loss = metrics.get("val_loss")
    throughput = metrics.get("throughput_samples_s")
    if throughput is None:
        return "throughput_samples_s missing"
    if val_loss is None:
        return "val_loss missing"
    if val_loss <= 0:
        return f"val_loss={val_loss} not positive"
    return None


def score_candidates(
    candidates: list[dict], *, efficiency_target: float = DEFAULT_EFFICIENCY_TARGET
) -> dict:
    """Score candidates by efficiency = throughput / val_loss; rank feasible ones, highest first."""
    feasible: list[dict] = []
    infeasible: list[dict] = []
    for candidate in candidates:
        metrics = candidate.get("metrics", {}) or {}
        entry = {
            "params": candidate.get("params"),
            "metrics": metrics,
            "val_loss": metrics.get("val_loss"),
            "throughput_samples_s": metrics.get("throughput_samples_s"),
        }
        reason = efficiency_failure(metrics)
        if reason:
            infeasible.append({**entry, "reasons": [reason]})
        else:
            entry["efficiency"] = metrics["throughput_samples_s"] / metrics["val_loss"]
            feasible.append(entry)

    ranked = sorted(feasible, key=lambda e: e["efficiency"], reverse=True)
    best = ranked[0] if ranked else None
    target_met = best is not None and best["efficiency"] >= efficiency_target
    return {
        "efficiency_target": efficiency_target,
        "ranked": ranked,
        "best": best,
        "target_met": target_met,
        "infeasible": infeasible,
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    raw = open(argv[0]).read() if argv else sys.stdin.read()
    data = json.loads(raw)
    if isinstance(data, dict):
        candidates = data.get("candidates", [])
        efficiency_target = data.get("efficiency_target", DEFAULT_EFFICIENCY_TARGET)
    else:
        candidates, efficiency_target = data, DEFAULT_EFFICIENCY_TARGET
    print(json.dumps(score_candidates(candidates, efficiency_target=efficiency_target), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
