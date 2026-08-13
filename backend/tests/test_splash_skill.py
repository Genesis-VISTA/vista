"""Validate the splash-planner skill: its campaign.yaml manifest + the candidate scorer."""

import importlib.util
from pathlib import Path

import vista_backend
from vista_backend.agents.campaign.manifest import load_manifest


SKILL_DIR = Path(vista_backend.__file__).parent / "db" / "skills" / "splash-planner"


def _load_scorer():
    path = SKILL_DIR / "scripts" / "score_candidates.py"
    spec = importlib.util.spec_from_file_location("splash_scorer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- manifest --------------------------------------------------------------


def test_campaign_manifest_is_valid():
    manifest = load_manifest(SKILL_DIR)
    assert manifest.domain == "splash"
    assert manifest.roles == ["neutronics", "chemistry"]
    assert manifest.metrics.primary.name == "TBR"
    assert manifest.metrics.primary.target == 1.1
    assert manifest.metrics.scorer == "scripts/score_candidates.py"
    assert {v.name for v in manifest.variables} == {
        "li6_enrichment",
        "temperature",
        "be_concentration",
        "blanket_thickness",
    }
    # Bound to the real simulation skills + HPC jobs.
    neutronics = manifest.subagent("neutronics")
    assert (
        neutronics.skill == "salt-neutronics-tbr"
        and neutronics.job == "salt-neutronics-tbr"
    )
    chemistry = manifest.subagent("chemistry")
    assert (
        chemistry.skill == "salt-chemistry-md" and chemistry.job == "salt-chemistry-md"
    )


# --- scorer ----------------------------------------------------------------


def _candidate(li6, *, tbr, density=2.0, **advisory):
    """A v1 candidate: TBR (neutronics) + density (chemistry), plus optional advisory metrics."""
    metrics = {"TBR": tbr, "density_g_cm3": density, **advisory}
    return {"params": {"li6_enrichment": li6}, "metrics": metrics}


def test_scorer_ranks_feasible_by_tbr_and_reports_target():
    scorer = _load_scorer()
    result = scorer.score_candidates(
        [
            _candidate(0.6, tbr=1.12),
            _candidate(0.8, tbr=1.20),
            _candidate(0.5, tbr=1.05),
        ],
        tbr_target=1.1,
    )
    assert [e["tbr"] for e in result["ranked"]] == [1.20, 1.12, 1.05]
    assert result["best"]["tbr"] == 1.20
    assert result["best"]["params"]["li6_enrichment"] == 0.8
    assert result["target_met"] is True
    assert result["infeasible"] == []


def test_scorer_density_gate_filters_infeasible():
    scorer = _load_scorer()
    result = scorer.score_candidates(
        [
            _candidate(0.9, tbr=1.30, density=3.1),  # density too high -> infeasible
            _candidate(0.8, tbr=1.25, density=None),  # density missing -> infeasible
            _candidate(0.6, tbr=1.12, density=2.0),  # in range -> feasible
        ],
        tbr_target=1.1,
    )
    # The two highest-TBR candidates are gated out on density; the in-range one wins.
    assert result["best"]["tbr"] == 1.12
    assert len(result["infeasible"]) == 2
    assert all(
        "density_g_cm3" in r for cand in result["infeasible"] for r in cand["reasons"]
    )


def test_scorer_target_not_met_when_best_below_target():
    scorer = _load_scorer()
    result = scorer.score_candidates([_candidate(0.5, tbr=1.05)], tbr_target=1.1)
    assert result["best"]["tbr"] == 1.05
    assert result["target_met"] is False


def test_advisory_metrics_are_reported_not_gated():
    scorer = _load_scorer()
    # A candidate that's density-feasible but would FAIL an advisory check (high melting point)
    # is still feasible/ranked; the advisory result is reported, not gating.
    result = scorer.score_candidates(
        [_candidate(0.7, tbr=1.15, density=2.0, melting_point_c=600.0)], tbr_target=1.1
    )
    assert result["infeasible"] == []
    best = result["best"]
    mp_check = next(c for c in best["advisory"] if c["metric"] == "melting_point_c")
    assert mp_check["ok"] is False  # reported as failing, but did not gate
    # Advisory metrics no candidate produced this cycle are flagged unmodeled.
    assert "viscosity_mpa_s" in result["advisory_unmodeled"]
    assert "melting_point_c" not in result["advisory_unmodeled"]  # was present here


def test_all_advisory_unmodeled_when_only_tbr_and_density():
    scorer = _load_scorer()
    result = scorer.score_candidates(
        [_candidate(0.7, tbr=1.15, density=2.0)], tbr_target=1.1
    )
    assert set(result["advisory_unmodeled"]) == {
        "melting_point_c",
        "boiling_point_c",
        "viscosity_mpa_s",
        "thermal_conductivity_w_mk",
        "cp_kj_kgk",
    }
    assert result["best"]["advisory"] == []
