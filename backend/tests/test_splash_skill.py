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
        "li6_enrichment", "temperature", "be_concentration", "blanket_thickness"
    }
    neutronics = manifest.subagent("neutronics")
    assert neutronics.skill == "neutronics-shift" and neutronics.job == "neutronics"
    chemistry = manifest.subagent("chemistry")
    assert chemistry.skill == "chemistry-supersalt" and chemistry.job == "chemistry"


# --- scorer ----------------------------------------------------------------

def _candidate(li6, *, tbr, mp=480.0, density=2.0, viscosity=8.0, k=1.0):
    return {
        "params": {"li6_enrichment": li6},
        "metrics": {
            "TBR": tbr, "melting_point_c": mp, "density_g_cm3": density,
            "viscosity_mpa_s": viscosity, "thermal_conductivity_w_mk": k,
        },
    }


def test_scorer_ranks_feasible_by_tbr_and_reports_target():
    scorer = _load_scorer()
    result = scorer.score_candidates(
        [_candidate(0.6, tbr=1.12), _candidate(0.8, tbr=1.20), _candidate(0.5, tbr=1.05)],
        tbr_target=1.1,
    )
    assert [e["tbr"] for e in result["ranked"]] == [1.20, 1.12, 1.05]
    assert result["best"]["tbr"] == 1.20
    assert result["best"]["params"]["li6_enrichment"] == 0.8
    assert result["target_met"] is True
    assert result["infeasible"] == []


def test_scorer_filters_chemistry_infeasible_candidates():
    scorer = _load_scorer()
    result = scorer.score_candidates(
        [
            _candidate(0.9, tbr=1.30, mp=600.0),       # melting point too high -> infeasible
            _candidate(0.7, tbr=1.15, viscosity=20.0),  # too viscous -> infeasible
            _candidate(0.6, tbr=1.12),                  # viable
        ],
        tbr_target=1.1,
    )
    # The highest-TBR candidate is infeasible; the best feasible one wins.
    assert result["best"]["tbr"] == 1.12
    assert len(result["infeasible"]) == 2
    assert any("melting_point_c" in r for r in result["infeasible"][0]["reasons"])


def test_scorer_target_not_met_when_best_below_target():
    scorer = _load_scorer()
    result = scorer.score_candidates([_candidate(0.5, tbr=1.05)], tbr_target=1.1)
    assert result["best"]["tbr"] == 1.05
    assert result["target_met"] is False
