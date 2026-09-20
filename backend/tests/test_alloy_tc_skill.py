"""Validate the alloy-tc-planner skill: its campaign.yaml manifest + the Tc scorer."""

import importlib.util
from pathlib import Path

import pytest

import vista_backend
from vista_backend.agents.campaign.manifest import load_manifest, render_script_args


SKILL_DIR = Path(vista_backend.__file__).parent / "db" / "skills" / "alloy-tc-planner"
SIM_SKILL_DIR = Path(vista_backend.__file__).parent / "db" / "skills" / "alloy-thermo-mc"
HPC_JOB_DIR = Path(vista_backend.__file__).resolve().parents[3] / "hpc_jobs" / "alloy-thermo-mc"


def _load_scorer():
    path = SKILL_DIR / "scripts" / "score_candidates.py"
    spec = importlib.util.spec_from_file_location("alloy_tc_scorer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candidate(mo=0.30, nb=0.25, ta=0.25, w=0.20, *, tc=1180.0, alpha=-0.31,
               bracketed=True, agree=True, swap=0.28):
    return {
        "params": {"mo": mo, "nb": nb, "ta": ta, "w": w},
        "metrics": {
            "Tc_cv_K": tc, "Tc_chi_K": tc + 60.0, "sro_alpha1": alpha,
            "swap_accept_mean": swap, "peak_bracketed": bracketed,
            "estimators_agree": agree,
        },
    }


# --- manifest --------------------------------------------------------------


def test_campaign_manifest_is_valid():
    manifest = load_manifest(SKILL_DIR)
    assert manifest.domain == "alloy-tc"
    assert manifest.roles == ["thermo"]
    assert manifest.metrics.primary.name == "Tc_cv_K"
    assert manifest.metrics.primary.direction == "maximize"
    assert manifest.metrics.scorer == "scripts/score_candidates.py"
    assert {v.name for v in manifest.variables} == {"mo", "nb", "ta", "w"}

    thermo = manifest.subagent("thermo")
    assert thermo.skill == "alloy-thermo-mc" and thermo.job == "alloy-thermo-mc"
    assert thermo.collect_files == ["results.json"]


def test_manifest_renders_flags_the_job_wrapper_actually_accepts():
    """The rendered script_args must parse against run_state_point.py's real parser."""
    manifest = load_manifest(SKILL_DIR)
    rendered = render_script_args(
        manifest, manifest.subagent("thermo"), {"mo": 0.30, "nb": 0.25, "ta": 0.25, "w": 0.20}
    )
    assert rendered == "--mo 0.3 --nb 0.25 --ta 0.25 --w 0.2"

    spec = importlib.util.spec_from_file_location(
        "alloy_wrapper", HPC_JOB_DIR / "run_state_point.py"
    )
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)

    # An unknown flag makes argparse exit; a silently-dropped one shows up as a
    # wrong parsed value. Both are caught here.
    args = wrapper.build_parser().parse_args(
        rendered.split()
        + ["--skill-root", "/tmp/clone",
           "--engine-bin", "/tmp/clone/engine/alloy_mc",
           "--output-dir", "/tmp/out"]
    )
    assert (args.mo, args.nb, args.ta, args.w) == (0.30, 0.25, 0.25, 0.20)


def test_every_mapped_flag_is_a_real_wrapper_option():
    """Guards against the manifest mapping drifting away from the job's argparse."""
    manifest = load_manifest(SKILL_DIR)
    spec = importlib.util.spec_from_file_location(
        "alloy_wrapper_opts", HPC_JOB_DIR / "run_state_point.py"
    )
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    known = {opt for action in wrapper.build_parser()._actions for opt in action.option_strings}

    thermo = manifest.subagent("thermo")
    for variable, flag in thermo.args.map.items():
        assert flag in known, f"manifest maps {variable} -> {flag}, not a wrapper option"



def test_sim_skill_and_job_exist_for_the_bound_role():
    manifest = load_manifest(SKILL_DIR)
    thermo = manifest.subagent("thermo")
    assert (SIM_SKILL_DIR / "SKILL.md").is_file(), f"missing sim skill {thermo.skill}"
    assert (HPC_JOB_DIR / "README.md").is_file(), f"missing hpc job {thermo.job}"
    assert (HPC_JOB_DIR / "cluster_defaults.json").is_file()


# --- scorer: composition gate ----------------------------------------------


def test_off_simplex_composition_is_infeasible():
    s = _load_scorer()
    result = s.score_candidates([_candidate(mo=0.50, nb=0.25, ta=0.25, w=0.20)])
    assert result["best"] is None
    assert "sums to" in result["infeasible"][0]["reasons"][0]


def test_negative_fraction_is_infeasible():
    s = _load_scorer()
    result = s.score_candidates([_candidate(mo=-0.05, nb=0.35, ta=0.45, w=0.25)])
    assert "negative" in result["infeasible"][0]["reasons"][0]


def test_user_bounds_are_enforced():
    s = _load_scorer()
    result = s.score_candidates([_candidate(mo=0.10, nb=0.30, ta=0.30, w=0.30)],
                                bounds={"mo": [0.2, 1.0]})
    assert result["best"] is None
    assert "outside requested bounds" in result["infeasible"][0]["reasons"][0]


def test_bounds_allow_a_compliant_candidate():
    s = _load_scorer()
    result = s.score_candidates([_candidate(mo=0.30)], bounds={"mo": [0.2, 1.0]})
    assert result["best"] is not None


# --- scorer: ordering gate -------------------------------------------------


def test_no_short_range_order_is_infeasible():
    """A Cv bump with alpha ~ 0 is a random solid solution, not a transition."""
    s = _load_scorer()
    result = s.score_candidates([_candidate(alpha=0.001)])
    assert result["best"] is None
    assert "no short-range order" in result["infeasible"][0]["reasons"][0]


def test_missing_sro_is_infeasible():
    s = _load_scorer()
    cand = _candidate()
    del cand["metrics"]["sro_alpha1"]
    result = s.score_candidates([cand])
    assert "sro_alpha1 missing" in result["infeasible"][0]["reasons"][0]


def test_sro_floor_is_configurable():
    s = _load_scorer()
    assert s.score_candidates([_candidate(alpha=-0.08)])["best"] is not None
    assert s.score_candidates([_candidate(alpha=-0.08)], sro_alpha_min=0.2)["best"] is None


# --- scorer: bracketing gate -----------------------------------------------


def test_unbracketed_peak_cannot_win_the_ranking():
    """The regression this gate exists for: an endpoint-pinned Tc looks like the best."""
    s = _load_scorer()
    good = _candidate(mo=0.30, tc=1180.0, bracketed=True)
    broken = _candidate(mo=0.40, nb=0.20, ta=0.20, w=0.20, tc=2000.0, bracketed=False)
    result = s.score_candidates([good, broken])
    assert result["best"]["tc"] == 1180.0, "a broken run out-ranked a good one"
    assert any("peak_bracketed=false" in r for r in result["infeasible"][0]["reasons"])


def test_bracketing_gate_can_be_demoted_to_advisory():
    s = _load_scorer()
    good = _candidate(mo=0.30, tc=1180.0, bracketed=True)
    broken = _candidate(mo=0.40, nb=0.20, ta=0.20, w=0.20, tc=2000.0, bracketed=False)
    result = s.score_candidates([good, broken], require_bracketed_peak=False)
    assert result["best"]["tc"] == 2000.0  # exactly the bias the default prevents


# --- scorer: ranking + target ----------------------------------------------


def test_ranks_feasible_candidates_by_tc_descending():
    s = _load_scorer()
    result = s.score_candidates([
        _candidate(mo=0.30, tc=1100.0),
        _candidate(mo=0.28, nb=0.24, ta=0.24, w=0.24, tc=1300.0),
        _candidate(mo=0.26, nb=0.26, ta=0.26, w=0.22, tc=1200.0),
    ])
    assert [e["tc"] for e in result["ranked"]] == [1300.0, 1200.0, 1100.0]
    assert result["best"]["tc"] == 1300.0


def test_target_met_reflects_the_users_threshold():
    s = _load_scorer()
    assert s.score_candidates([_candidate(tc=1300.0)], tc_target=1250.0)["target_met"] is True
    assert s.score_candidates([_candidate(tc=1200.0)], tc_target=1250.0)["target_met"] is False


def test_advisory_signals_are_reported_but_never_gate():
    s = _load_scorer()
    result = s.score_candidates([_candidate(agree=False, swap=0.02)])
    assert result["best"] is not None, "advisory signals must not reject a candidate"
    advisory = {c["metric"]: c["ok"] for c in result["best"]["advisory"]}
    assert advisory == {"estimators_agree": False, "swap_accept_mean": False}


def test_empty_cycle_is_handled():
    s = _load_scorer()
    result = s.score_candidates([])
    assert result["best"] is None and result["target_met"] is False


@pytest.mark.parametrize("bad", [{"mo": 0.5, "nb": 0.5}, {}, None])
def test_missing_composition_keys_are_infeasible(bad):
    s = _load_scorer()
    result = s.score_candidates([{"params": bad, "metrics": {"Tc_cv_K": 1200.0}}])
    assert result["best"] is None
