#!/usr/bin/env python3
"""
Alloy Tc campaign candidate scorer (v1).

The planner runs this in the sandbox over the cycle's collected results:

    python3 score_candidates.py results.json     # or pipe JSON on stdin

Input JSON:
    {"tc_target": 1250,
     "bounds": {"mo": [0.2, 1.0]},          # optional per-element floors/ceilings
     "candidates": [{"params": {"mo":…, "nb":…, "ta":…, "w":…},
                     "metrics": {...}}, ...]}

Metric keys produced by the `alloy-thermo-mc` simulation (its results.json `metrics`):
    Tc_cv_K           — transition temperature from the specific-heat peak  (PRIMARY)
    Tc_chi_K          — same from the susceptibility peak (cross-check)
    sro_alpha1        — mean Warren-Cowley SRO parameter at the lowest ladder temperature
    swap_accept_mean  — mean replica-exchange acceptance across the ladder
    peak_bracketed    — did the T ladder actually bracket the Cv peak?
    estimators_agree  — do the Cv- and chi-peak estimates agree within 15%?

Scoring (v1) — maximize Tc subject to constraints:

  - Primary (ranked): maximize `Tc_cv_K`. `tc_target` is a stopping threshold, not a
    ranking term; a candidate above it is "target met", it is not scored differently.

  - HARD gate — composition. The four fractions must be non-negative and sum to 1.0
    (+/- 1e-3), and must satisfy any per-element `bounds`. This is re-checked here even
    though the planner validates pre-dispatch and the job wrapper rejects bad sums: a
    candidate that reached this scorer off-simplex means something upstream is wrong,
    and silently ranking it would hide that.

  - HARD gate — ordering character. |`sro_alpha1`| must be >= `sro_alpha_min`. A
    specific-heat bump with no short-range order is not an order-disorder transition,
    so a candidate that "peaks" without ordering is rejected rather than ranked.

  - HARD gate — bracketed peak (`require_bracketed_peak`, default true). See the note
    below; this one is a deliberate deviation from "run quality is advisory".

  - ADVISORY (reported, never gates or ranks): `estimators_agree`, `swap_accept_mean`.

Why `peak_bracketed` gates by default
-------------------------------------
An unbracketed peak is not merely noisy — it is *biased upward*. When the ladder fails
to bracket the transition, `analyze.py` reports the peak at a ladder endpoint, which is
`T_final` (the high end). Such a run therefore looks like the HIGHEST-Tc candidate in
the cycle and would win a maximize-Tc ranking outright. Treating it as advisory would
let broken runs systematically out-compete good ones and steer the whole campaign.

Set `"require_bracketed_peak": false` in the input to demote it to advisory, but expect
the ranking to favor runs whose ladder missed the transition.
"""
from __future__ import annotations

import json
import sys


DEFAULT_TC_TARGET = 1250.0
ELEMENTS = ("mo", "nb", "ta", "w")

# Composition must lie on the simplex; same tolerance the job wrapper enforces.
SIMPLEX_TOL = 1e-3

# Ordering-character gate. |alpha| ~ 0 is a random solid solution; appreciable |alpha|
# means genuine short-range order. This floor is a HEURISTIC screening value, not a
# derived physical threshold — revisit it once real campaign data exists.
DEFAULT_SRO_ALPHA_MIN = 0.05

# Advisory band for replica-exchange acceptance (the skill's guidance is ~20-40%).
SWAP_ACCEPT_MIN, SWAP_ACCEPT_MAX = 0.2, 0.4


def composition_gate_failure(params: dict | None, bounds: dict | None) -> str | None:
    """Return a reason if the composition is off-simplex, negative, or out of bounds."""
    if not params:
        return "params missing"
    missing = [e for e in ELEMENTS if params.get(e) is None]
    if missing:
        return f"composition missing {', '.join(missing)}"

    values = {e: float(params[e]) for e in ELEMENTS}
    negative = [f"{e}={v}" for e, v in values.items() if v < 0.0]
    if negative:
        return f"negative fraction(s): {', '.join(negative)}"

    total = sum(values.values())
    if abs(total - 1.0) > SIMPLEX_TOL:
        return f"composition sums to {total:.6f}, not 1.0 +/- {SIMPLEX_TOL}"

    for element, (lo, hi) in (bounds or {}).items():
        key = element.lower()
        if key not in values:
            continue
        if not (lo <= values[key] <= hi):
            return f"{key}={values[key]} outside requested bounds [{lo}, {hi}]"
    return None


def sro_gate_failure(metrics: dict, *, sro_alpha_min: float) -> str | None:
    """Return a reason if the run shows no genuine short-range ordering."""
    alpha = metrics.get("sro_alpha1")
    if alpha is None:
        return "sro_alpha1 missing (cannot confirm a real transition)"
    if abs(alpha) < sro_alpha_min:
        return (
            f"|sro_alpha1|={abs(alpha):.4f} < {sro_alpha_min} "
            "(no short-range order: Cv bump is not an order-disorder transition)"
        )
    return None


def bracketing_gate_failure(metrics: dict) -> str | None:
    """Return a reason if the T ladder did not bracket the Cv peak (see module docstring)."""
    bracketed = metrics.get("peak_bracketed")
    if bracketed is None:
        return "peak_bracketed missing (cannot trust Tc)"
    if not bracketed:
        return "peak_bracketed=false (Tc pinned to a ladder endpoint; widen --t-init/--t-final)"
    return None


def advisory_checks(metrics: dict) -> list[dict]:
    """Evaluate advisory run-quality signals present in `metrics` (never gates or ranks)."""
    checks: list[dict] = []
    agree = metrics.get("estimators_agree")
    if agree is not None:
        checks.append({
            "metric": "estimators_agree",
            "value": agree,
            "target": "Cv- and chi-peak Tc within 15%",
            "ok": bool(agree),
        })
    swap = metrics.get("swap_accept_mean")
    if swap is not None:
        checks.append({
            "metric": "swap_accept_mean",
            "value": swap,
            "target": f"{SWAP_ACCEPT_MIN}-{SWAP_ACCEPT_MAX}",
            "ok": SWAP_ACCEPT_MIN <= swap <= SWAP_ACCEPT_MAX,
        })
    return checks


def score_candidates(
    candidates: list[dict],
    *,
    tc_target: float = DEFAULT_TC_TARGET,
    bounds: dict | None = None,
    sro_alpha_min: float = DEFAULT_SRO_ALPHA_MIN,
    require_bracketed_peak: bool = True,
) -> dict:
    """Gate on composition + ordering (+ bracketing), then rank survivors by Tc."""
    feasible: list[dict] = []
    infeasible: list[dict] = []

    for candidate in candidates:
        params = candidate.get("params") or {}
        metrics = candidate.get("metrics", {}) or {}
        entry = {
            "params": params,
            "metrics": metrics,
            "tc": metrics.get("Tc_cv_K"),
            "advisory": advisory_checks(metrics),
        }
        gates = [
            composition_gate_failure(params, bounds),
            sro_gate_failure(metrics, sro_alpha_min=sro_alpha_min),
        ]
        if require_bracketed_peak:
            gates.append(bracketing_gate_failure(metrics))
        reasons = [r for r in gates if r is not None]
        if reasons:
            infeasible.append({**entry, "reasons": reasons})
        else:
            feasible.append(entry)

    # Rank feasible candidates with a known Tc, highest first.
    ranked = sorted(
        [e for e in feasible if e["tc"] is not None], key=lambda e: e["tc"], reverse=True
    )
    best = ranked[0] if ranked else None
    target_met = best is not None and best["tc"] >= tc_target
    return {
        "tc_target": tc_target,
        "sro_alpha_min": sro_alpha_min,
        "require_bracketed_peak": require_bracketed_peak,
        "ranked": ranked,
        "best": best,
        "target_met": target_met,
        "infeasible": infeasible,
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    raw = open(argv[0], encoding="utf-8").read() if argv else sys.stdin.read()
    data = json.loads(raw)
    if isinstance(data, dict):
        candidates = data.get("candidates", [])
        kwargs = {
            "tc_target": data.get("tc_target", DEFAULT_TC_TARGET),
            "bounds": data.get("bounds"),
            "sro_alpha_min": data.get("sro_alpha_min", DEFAULT_SRO_ALPHA_MIN),
            "require_bracketed_peak": data.get("require_bracketed_peak", True),
        }
    else:
        candidates, kwargs = data, {}
    print(json.dumps(score_candidates(candidates, **kwargs), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
