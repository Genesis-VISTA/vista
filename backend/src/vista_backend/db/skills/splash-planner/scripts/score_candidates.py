#!/usr/bin/env python3
"""
SPLASH candidate scorer (v1).

The planner runs this in the sandbox over the cycle's collected results:

    python3 score_candidates.py results.json     # or pipe JSON on stdin

Input JSON: {"tbr_target": 1.1, "candidates": [{"params": {...}, "metrics": {...}}, ...]}

v1 metric keys actually produced by the simulations:
    TBR                         — salt-neutronics-tbr (neutronics)
    shielding_flux_n_per_cm2_s  — salt-neutronics-tbr (neutronics; magnet flux at a 1 m blanket,
                                  from the tbr results.json `shielding.magnet_flux_n_per_cm2_s`)
    density_g_cm3               — salt-chemistry-md  (chemistry; from results.json density.density_g_cm3)

Scoring (v1):
  - Primary (ranked): maximize TBR (target > tbr_target).
  - Hard viability gate (scored): density_g_cm3 in [1.8, 2.5]. Missing or out-of-range -> infeasible.
  - Hard shielding gate (scored): magnet radiation flux at 1 m must be <= 1e12 n/cm²·s, else the
    blanket shields the superconducting magnets too weakly -> reject. <= 1e10 is the preferred
    (long magnet-life) target, reported but not required. A missing flux is NOT gated (reported as
    not-evaluated) so candidates aren't dropped when the neutronics job didn't surface shielding.
  - Advisory only (never gates or ranks): melting/boiling point, viscosity, thermal conductivity,
    Cp. Reported when present; v1 sims don't compute them, so they're listed as unmodeled.
"""
from __future__ import annotations

import json
import sys


DEFAULT_TBR_TARGET = 1.1

# Hard, sim-backed viability gate (the only chemistry constraint v1 can actually evaluate).
DENSITY_MIN_G_CM3, DENSITY_MAX_G_CM3 = 1.8, 2.5

# Hard, sim-backed magnet radiation-shielding gate (neutronics). Flux (n/cm²·s) reaching the
# superconducting magnets behind a 1 m blanket: above the reject limit the fast-neutron dose
# damages them over the plant lifetime; the preferred limit is the long-magnet-life target.
SHIELDING_METRIC_KEY = "shielding_flux_n_per_cm2_s"
SHIELDING_REJECT_ABOVE = 1.0e12
SHIELDING_PREFERRED_AT_OR_BELOW = 1.0e10

# Advisory criteria (from the scientific playbook / evaluation-metrics.txt): (key, target, predicate).
# Reported, never gating or ranking — v1 simulations don't compute most of them.
ADVISORY = [
    ("melting_point_c", "< 550 °C", lambda v: v < 550.0),
    ("boiling_point_c", "> 1000 °C", lambda v: v > 1000.0),
    ("viscosity_mpa_s", "< 15 mPa·s", lambda v: v < 15.0),
    ("thermal_conductivity_w_mk", "> 0.8 W/(m·K)", lambda v: v > 0.8),
    ("cp_kj_kgk", "> 1.5 kJ/(kg·K)", lambda v: v > 1.5),
]


def density_gate_failure(metrics: dict) -> str | None:
    """Return a reason string if the density viability gate fails, else None."""
    density = metrics.get("density_g_cm3")
    if density is None:
        return "density_g_cm3 missing"
    if not (DENSITY_MIN_G_CM3 <= density <= DENSITY_MAX_G_CM3):
        return f"density_g_cm3={density} not in [{DENSITY_MIN_G_CM3}, {DENSITY_MAX_G_CM3}]"
    return None


def shielding_gate_failure(metrics: dict) -> str | None:
    """Return a reason string if the magnet-shielding hard gate fails, else None.

    Only rejects when a flux is present and exceeds the reject limit; a missing value is not
    gated (see `shielding_status`) so candidates aren't dropped when the neutronics job/parser
    didn't surface shielding.
    """
    flux = metrics.get(SHIELDING_METRIC_KEY)
    if flux is None:
        return None
    if flux > SHIELDING_REJECT_ABOVE:
        return f"{SHIELDING_METRIC_KEY}={flux:.3e} > {SHIELDING_REJECT_ABOVE:.0e} (magnet shielding too weak)"
    return None


def shielding_status(metrics: dict) -> dict:
    """Report the magnet shielding flux + verdict for a candidate (never gates here)."""
    flux = metrics.get(SHIELDING_METRIC_KEY)
    if flux is None:
        return {"flux_n_per_cm2_s": None, "verdict": "not_evaluated"}
    if flux > SHIELDING_REJECT_ABOVE:
        verdict = "reject"
    elif flux <= SHIELDING_PREFERRED_AT_OR_BELOW:
        verdict = "preferred"
    else:
        verdict = "acceptable"
    return {"flux_n_per_cm2_s": flux, "verdict": verdict}


def advisory_checks(metrics: dict) -> list[dict]:
    """Evaluate advisory criteria that are present in `metrics` (never gates or ranks)."""
    checks: list[dict] = []
    for key, target, predicate in ADVISORY:
        value = metrics.get(key)
        if value is not None:
            checks.append({"metric": key, "value": value, "target": target, "ok": predicate(value)})
    return checks


def score_candidates(candidates: list[dict], *, tbr_target: float = DEFAULT_TBR_TARGET) -> dict:
    """Gate on density, rank feasible candidates by TBR, and report advisories."""
    feasible: list[dict] = []
    infeasible: list[dict] = []
    advisory_seen: set[str] = set()
    for candidate in candidates:
        metrics = candidate.get("metrics", {}) or {}
        checks = advisory_checks(metrics)
        advisory_seen.update(c["metric"] for c in checks)
        entry = {
            "params": candidate.get("params"),
            "metrics": metrics,
            "tbr": metrics.get("TBR"),
            "shielding": shielding_status(metrics),
            "advisory": checks,
        }
        reasons = [
            r
            for r in (density_gate_failure(metrics), shielding_gate_failure(metrics))
            if r is not None
        ]
        if reasons:
            infeasible.append({**entry, "reasons": reasons})
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
    advisory_unmodeled = [key for key, _t, _p in ADVISORY if key not in advisory_seen]
    return {
        "tbr_target": tbr_target,
        "ranked": ranked,
        "best": best,
        "target_met": target_met,
        "infeasible": infeasible,
        "advisory_unmodeled": advisory_unmodeled,
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
