#!/usr/bin/env python3
"""
SPLASH candidate scorer (v1, focused viability set).

The planner runs this in the sandbox over the cycle's collected results:

    python3 score_candidates.py results.json     # or pipe JSON on stdin

Input JSON: {"tbr_target": 1.1, "candidates": [{"params": {...}, "metrics": {...}}, ...]}
Each candidate's metrics use these keys (what the v1 stub sims emit):
    TBR, melting_point_c, density_g_cm3, viscosity_mpa_s, thermal_conductivity_w_mk

Rule: maximize TBR among candidates that pass the chemistry-viability filter; a candidate that
misses TBR > target is not a solution even if viable. Boiling point / Cp / ionic diffusion /
corrosion / tritium-affinity are NOT modeled in v1.
"""
from __future__ import annotations

import json
import sys


DEFAULT_TBR_TARGET = 1.1

# Viability thresholds (from evaluation-metrics.txt; the focused v1 subset).
MELTING_POINT_MAX_C = 550.0
DENSITY_MIN_G_CM3, DENSITY_MAX_G_CM3 = 1.8, 2.5
VISCOSITY_MAX_MPA_S = 15.0
THERMAL_CONDUCTIVITY_MIN_W_MK = 0.8


def viability_failures(metrics: dict) -> list[str]:
    """Return the list of viability constraints this candidate fails (empty == viable)."""
    failures: list[str] = []

    mp = metrics.get("melting_point_c")
    if mp is None or mp >= MELTING_POINT_MAX_C:
        failures.append(f"melting_point_c={mp} not < {MELTING_POINT_MAX_C}")

    density = metrics.get("density_g_cm3")
    if density is None or not (DENSITY_MIN_G_CM3 <= density <= DENSITY_MAX_G_CM3):
        failures.append(f"density_g_cm3={density} not in [{DENSITY_MIN_G_CM3}, {DENSITY_MAX_G_CM3}]")

    viscosity = metrics.get("viscosity_mpa_s")
    if viscosity is None or viscosity >= VISCOSITY_MAX_MPA_S:
        failures.append(f"viscosity_mpa_s={viscosity} not < {VISCOSITY_MAX_MPA_S}")

    k = metrics.get("thermal_conductivity_w_mk")
    if k is None or k <= THERMAL_CONDUCTIVITY_MIN_W_MK:
        failures.append(f"thermal_conductivity_w_mk={k} not > {THERMAL_CONDUCTIVITY_MIN_W_MK}")

    return failures


def score_candidates(candidates: list[dict], *, tbr_target: float = DEFAULT_TBR_TARGET) -> dict:
    """Filter by viability, rank the feasible ones by TBR, and report the decision."""
    feasible: list[dict] = []
    infeasible: list[dict] = []
    for candidate in candidates:
        metrics = candidate.get("metrics", {}) or {}
        entry = {"params": candidate.get("params"), "metrics": metrics, "tbr": metrics.get("TBR")}
        failures = viability_failures(metrics)
        if failures:
            infeasible.append({**entry, "reasons": failures})
        else:
            feasible.append(entry)

    # Rank feasible candidates with a known TBR, highest first.
    ranked = sorted(
        [e for e in feasible if e["tbr"] is not None],
        key=lambda e: e["tbr"],
        reverse=True,
    )
    best = ranked[0] if ranked else None
    target_met = best is not None and best["tbr"] >= tbr_target
    return {
        "tbr_target": tbr_target,
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
        tbr_target = data.get("tbr_target", DEFAULT_TBR_TARGET)
    else:
        candidates, tbr_target = data, DEFAULT_TBR_TARGET
    print(json.dumps(score_candidates(candidates, tbr_target=tbr_target), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
