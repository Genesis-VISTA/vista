"""
Contract tests for the `refine-downscaling` HPC job.

Covers the two halves vista owns: the Frontier submission defaults in
`cluster_defaults.json` plus `job.frontier.slurm`, and `run_downscaling.py` — the
wrapper that turns the skill's `script_args` into upstream command lines,
preflights the pre-staged assets, and composes `results.json`.

Hermetic: the REFINE pipelines are stubbed with scripts that record the argv they
were handed and emit the same JSON the real ones do, so nothing here needs
Frontier, a GPU, the ROCm conda env, or the staged Daymet assets. The stubs are
the *contract* — if upstream's CLI or output shape drifts, these tests keep
passing and the Frontier run breaks, which is why task group 6 exists.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from vista_mcp_server.submit_job_mcp import ClusterDefaults

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
JOB_DIR = REPO_ROOT / "hpc_jobs" / "refine-downscaling"

# From the demo package's model registry: the released 6x checkpoint.
REGISTRY_SHA_FIELD = "checkpoint_sha256"

# Stubs that behave like the upstream pipelines: record argv, write the JSON.
INFER_STUB = """\
import argparse, json, sys
from pathlib import Path
p = argparse.ArgumentParser()
for f in ("--data-dir","--checkpoint","--output","--start-date","--start-index",
          "--end-index","--format","--batch-size"):
    p.add_argument(f)
p.add_argument("--input", action="append")
p.add_argument("--amp", action="store_true")
p.add_argument("--enforce-temperature-order", action="store_true")
a = p.parse_args()
Path(a.output).parent.joinpath("argv_infer.json").write_text(json.dumps(sys.argv[1:]))
Path(a.output).write_bytes(b"\\0" * 4096)
Path(str(a.output) + ".json").write_text(json.dumps({
    "variables": ["tmin", "tmax", "prcp"],
    "output_timesteps": int(a.end_index) - int(a.start_index),
    "start_date": a.start_date,
    "source_start_index": int(a.start_index),
    "source_end_index": int(a.end_index),
    "temperature_order_enforced": True,
    "variable_metadata": {"tmin": {"units": "degC"}, "tmax": {"units": "degC"},
                          "prcp": {"units": "mm/day"}},
}))
"""

EVALUATE_STUB = """\
import argparse, json, sys
from pathlib import Path
p = argparse.ArgumentParser()
for f in ("--data-dir","--checkpoint","--output-dir","--split","--max-days","--batch-size"):
    p.add_argument(f)
p.add_argument("--amp", action="store_true")
p.add_argument("--enforce-temperature-order", action="store_true")
p.add_argument("--no-save-predictions", action="store_true")
a = p.parse_args()
d = Path(a.output_dir); d.mkdir(parents=True, exist_ok=True)
d.parent.joinpath("argv_evaluate.json").write_text(json.dumps(sys.argv[1:]))
if not a.no_save_predictions:
    (d / "predictions.npy").write_bytes(b"\\0" * 8192)
def m(bias, mae, rmse): return {"bias": bias, "mae": mae, "rmse": rmse}
(d / "evaluation_summary.json").write_text(json.dumps({
    "split": a.split, "days": int(a.max_days), "parameters": 5181483,
    "variables": ["tmin", "tmax", "prcp"], "storage_layout": "netcdf_patch_index",
    "temperature_order_enforced": True, "temperature_order_violation_fraction": 0.0142,
    "variable_metadata": {"tmin": {"units": "degC"}, "tmax": {"units": "degC"},
                          "prcp": {"units": "mm/day"}},
    "metrics": {
        "tmin": {"model": m(-0.00102, 0.04966, 0.16798),
                 "bilinear_baseline": m(-0.00067, 0.20163, 0.99392),
                 "mae_improvement_percent": 75.37, "count": 1545894720},
        "tmax": {"model": m(0.00139, 0.04873, 0.15347),
                 "bilinear_baseline": m(0.00100, 0.20118, 0.84968),
                 "mae_improvement_percent": 75.78, "count": 1545894720},
        "prcp": {"model": m(-0.00094, 0.04954, 0.31374),
                 "bilinear_baseline": m(0.0, 0.13467, 0.58905),
                 "mae_improvement_percent": 63.22, "count": 1545894720},
    },
}))
"""

SPATIAL_STUB = """\
import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
for f in ("--data-dir","--evaluation-dir","--split","--tile-rows"):
    p.add_argument(f)
a = p.parse_args()
d = Path(a.evaluation_dir)
d.parent.joinpath("argv_spatial.json").write_text(json.dumps([a.split, a.tile_rows]))
(d / "spatial_statistics_1990.npz").write_bytes(b"\\0" * 2048)
plots = [str(d / f"{s}_comparison.png") for s in ("mean_tmin", "p95_prcp")]
for q in plots:
    Path(q).write_bytes(b"\\211PNG\\r\\n\\032\\n")
(d / "spatial_statistics_index.json").write_text(json.dumps({
    "split": a.split, "days": 1, "variables": ["tmin", "tmax", "prcp"],
    "statistics": ["mean_tmin", "p95_prcp"], "plots": plots,
}))
"""


def _load_wrapper():
    path = JOB_DIR / "run_downscaling.py"
    spec = importlib.util.spec_from_file_location("refine_run_downscaling", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def demo(tmp_path):
    """A stand-in for the read-only Lustre demo package."""
    base = tmp_path / "demo"
    (base / "skill-authoring-kit").mkdir(parents=True)
    (base / "pipeline_04_infer.py").write_text(INFER_STUB)
    (base / "pipeline_03_evaluate.py").write_text(EVALUATE_STUB)
    (base / "utility_plot_spatial_statistics.py").write_text(SPATIAL_STUB)

    checkpoint = tmp_path / "best.pt"
    checkpoint.write_bytes(b"weights" * 100)
    (base / "skill-authoring-kit" / "model-registry.json").write_text(
        json.dumps(
            {
                "models": {
                    "stage2": {
                        REGISTRY_SHA_FIELD: hashlib.sha256(
                            checkpoint.read_bytes()
                        ).hexdigest()
                    }
                }
            }
        )
    )

    data = tmp_path / "prepared"
    data.mkdir()
    (data / "manifest.json").write_text(
        json.dumps(
            {
                "storage_layout": "netcdf_patch_index",
                "splits": {"train": {}, "val": {}, "test": {}},
                "year_lengths": {"1989": 365, "1990": 365},
            }
        )
    )

    inputs = tmp_path / "data"
    inputs.mkdir()
    for variable in ("tmin", "tmax", "prcp"):
        (inputs / f"Daymet_ERA5_{variable}_dy_1990_0p25deg.nc").write_bytes(b"nc")

    out = tmp_path / "out"
    out.mkdir()
    return {
        "base": base,
        "data": data,
        "inputs": inputs,
        "checkpoint": checkpoint,
        "out": out,
        "env": base,  # any existing dir stands in for the conda prefix
    }


def run(demo, monkeypatch, *script_args, capture=None):
    """Invoke the wrapper the way job.frontier.slurm does. Returns the exit code."""
    module = _load_wrapper()
    argv = [
        "run_downscaling.py",
        "--base-dir",
        str(demo["base"]),
        "--data-dir",
        str(demo["data"]),
        "--input-dir",
        str(demo["inputs"]),
        "--checkpoint",
        str(demo["checkpoint"]),
        "--env-prefix",
        str(demo["env"]),
        "--output-dir",
        str(demo["out"]),
        *script_args,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    return module.main()


def results_of(demo):
    return json.loads((demo["out"] / "results.json").read_text())


def recorded(demo, name):
    return json.loads((demo["out"] / f"argv_{name}.json").read_text())


# --- cluster_defaults ------------------------------------------------------


def test_cluster_defaults_are_frontier_only_and_gpu_shaped():
    defaults = ClusterDefaults.model_validate_json(
        (JOB_DIR / "cluster_defaults.json").read_text()
    )
    assert defaults.odo is None and defaults.perlmutter is None
    frontier = defaults.frontier
    assert frontier is not None
    assert frontier.resources.node_count == 1
    # Exclusive so Frontier's prolog binds all 8 GCDs; the inner srun takes one.
    assert frontier.resources.exclusive_node_use is True
    assert frontier.iri.queue_name == "batch"
    # A placeholder until a real run is timed (design open question 1), but it must
    # stay long enough to be plausible for GPU inference.
    assert frontier.duration >= 600


def test_cluster_defaults_declare_every_staged_asset():
    """
    Relocating any staged asset must be a config edit, not a code change — so each
    one has to be addressable here (spec: GPU catalog entries declare their
    environment dependency).
    """
    defaults = ClusterDefaults.model_validate_json(
        (JOB_DIR / "cluster_defaults.json").read_text()
    )
    environment = defaults.frontier.iri.environment
    assert set(environment) >= {
        "REFINE_BASE_DIR",
        "REFINE_ENV",
        "REFINE_CHECKPOINT",
        "REFINE_DATA_DIR",
        "REFINE_INPUT_DIR",
    }
    # world-shared, not proj-shared: vista submits under a different project than
    # the one that owns the demo, so proj-shared would not be readable.
    assert "/world-shared/" in environment["REFINE_BASE_DIR"]
    assert "/world-shared/" in environment["REFINE_ENV"]


# --- job.frontier.slurm ----------------------------------------------------


def test_frontier_script_declares_no_sbatch_directives():
    script = (JOB_DIR / "job.frontier.slurm").read_text()
    assert not [
        line for line in script.splitlines() if line.strip().startswith("#SBATCH")
    ]
    assert "#SBATCH" in script, "keep the comment explaining why there are none"


def test_frontier_script_drops_inherited_pythonpath_before_module_load():
    """
    The dispatcher leaves `inherit_environment` on, so the IRI host's PYTHONPATH
    arrives with the job. A foreign PYTHONPATH ahead of a ROCm torch segfaults at
    import rather than raising, so it must go before any module is loaded.
    """
    script = (JOB_DIR / "job.frontier.slurm").read_text()
    assert "unset PYTHONPATH" in script
    assert script.index("unset PYTHONPATH") < script.index("module load")


def test_frontier_script_activates_the_env_and_refuses_to_build_one():
    script = (JOB_DIR / "job.frontier.slurm").read_text()
    assert 'conda activate "${REFINE_ENV}"' in script
    assert "ERROR: conda env not found" in script
    for forbidden in ("pip install", "conda create", "python3 -m venv"):
        assert forbidden not in script, f"the job must never build an env ({forbidden})"


def test_frontier_script_binds_one_gpu_through_an_inner_srun():
    """Frontier's IRI service runs the script once on the head node; without an
    inner srun nothing binds a GCD."""
    script = (JOB_DIR / "job.frontier.slurm").read_text()
    assert "srun" in script
    assert "--gpus-per-task=1" in script
    assert "--gpu-bind=closest" in script


def test_miopen_caches_go_to_node_local_tmp_not_lustre():
    """MIOpen keeps a SQLite perf DB, and SQLite over Lustre locks badly."""
    script = (JOB_DIR / "job.frontier.slurm").read_text()
    cache_line = next(line for line in script.splitlines() if "_cache_root=" in line)
    assert "/tmp/" in cache_line
    assert "VISTA_OUT" not in cache_line
    assert "SLURM_JOB_ID" in cache_line, "tag by job id so runs cannot collide"


def test_frontier_script_never_works_inside_the_readonly_demo_root():
    script = (JOB_DIR / "job.frontier.slurm").read_text()
    assert 'cd "${VISTA_OUT}"' in script
    assert 'cd "${REFINE_BASE_DIR}"' not in script


# --- argument validation ---------------------------------------------------


def test_plots_outside_evaluate_is_refused_before_any_work(demo, monkeypatch):
    assert run(demo, monkeypatch, "--plots") == 1
    assert not (demo["out"] / "results.json").exists()
    assert not (demo["out"] / "argv_infer.json").exists(), "no pipeline may have run"


@pytest.mark.parametrize(
    "mode, flag",
    [
        ("infer", "--max-days"),
        ("infer", "--split"),
        ("infer", "--tile-rows"),
        ("evaluate", "--days"),
        ("evaluate", "--start-date"),
    ],
)
def test_a_flag_from_the_other_mode_is_an_error_not_a_no_op(
    demo, monkeypatch, mode, flag
):
    """A silently ignored flag is worse than a refusal: the user believes it took."""
    value = (
        "test"
        if flag == "--split"
        else ("1990-01-01" if flag == "--start-date" else "2")
    )
    assert run(demo, monkeypatch, "--mode", mode, flag, value) == 1
    assert not (demo["out"] / "results.json").exists()


@pytest.mark.parametrize(
    "mode, flag", [("infer", "--days"), ("evaluate", "--max-days")]
)
def test_zero_or_negative_days_is_refused(demo, monkeypatch, mode, flag):
    assert run(demo, monkeypatch, "--mode", mode, flag, "0") == 1


# --- the day guardrail -----------------------------------------------------


@pytest.mark.parametrize(
    "mode, flag", [("infer", "--days"), ("evaluate", "--max-days")]
)
def test_more_than_31_days_is_refused_without_allow_large(
    demo, monkeypatch, mode, flag, capsys
):
    assert run(demo, monkeypatch, "--mode", mode, flag, "365") == 1
    message = capsys.readouterr().err
    assert "guardrail" in message
    assert "GB" in message, "a refusal must make the cost concrete"
    assert "--allow-large" in message, "and name the way through"


def test_allow_large_permits_the_long_run(demo, monkeypatch):
    assert run(demo, monkeypatch, "--days", "40", "--allow-large") == 0
    assert results_of(demo)["run"]["output_timesteps"] == 40


def test_the_guardrail_sits_at_31_days(demo, monkeypatch):
    assert run(demo, monkeypatch, "--days", "31") == 0


# --- interval arithmetic ---------------------------------------------------


def test_start_date_maps_onto_the_pipelines_index_arithmetic(demo, monkeypatch):
    """
    Inputs are one file per calendar year, so index zero is 1 January. The pipeline
    gets that date plus an offset, never the user's date as index zero.
    """
    assert run(demo, monkeypatch, "--start-date", "1990-03-02", "--days", "3") == 0
    argv = recorded(demo, "infer")
    assert argv[argv.index("--start-date") + 1] == "1990-01-01"
    assert argv[argv.index("--start-index") + 1] == "60"  # 31 + 28
    assert argv[argv.index("--end-index") + 1] == "63"  # exclusive


def test_an_interval_crossing_a_year_boundary_is_refused(demo, monkeypatch, capsys):
    assert run(demo, monkeypatch, "--start-date", "1990-12-20", "--days", "20") == 1
    assert "past the end of 1990" in capsys.readouterr().err


def test_a_year_with_no_staged_inputs_names_the_missing_file(demo, monkeypatch, capsys):
    assert run(demo, monkeypatch, "--start-date", "1975-01-01") == 1
    assert "Daymet_ERA5_tmin_dy_1975_0p25deg.nc" in capsys.readouterr().err


def test_a_malformed_start_date_is_refused(demo, monkeypatch, capsys):
    assert run(demo, monkeypatch, "--start-date", "March 2nd") == 1
    assert "YYYY-MM-DD" in capsys.readouterr().err


# --- preflight -------------------------------------------------------------


@pytest.mark.parametrize("asset", ["base", "data", "checkpoint", "env"])
def test_a_missing_staged_asset_names_its_exact_path(demo, monkeypatch, asset, capsys):
    missing = demo["base"].parent / "gone"
    demo[asset] = missing
    assert run(demo, monkeypatch) == 1
    error = capsys.readouterr().err
    assert str(missing) in error
    assert "cannot be recreated by resubmitting" in error


def test_a_checkpoint_that_is_not_the_released_model_is_fatal(
    demo, monkeypatch, capsys
):
    demo["checkpoint"].write_bytes(b"different weights")
    assert run(demo, monkeypatch) == 1
    error = capsys.readouterr().err
    assert "SHA256 mismatch" in error
    assert "not comparable" in error


def test_an_absent_registry_warns_but_still_records_the_hash(demo, monkeypatch):
    """No registry is a degraded check, not a reason to refuse a run."""
    (demo["base"] / "skill-authoring-kit" / "model-registry.json").unlink()
    assert run(demo, monkeypatch) == 0
    results = results_of(demo)
    expected = hashlib.sha256(demo["checkpoint"].read_bytes()).hexdigest()
    assert results["provenance"]["checkpoint_sha256"] == expected
    assert any("not verified" in w for w in results["warnings"])


def test_an_unknown_split_is_refused(demo, monkeypatch):
    """argparse bounds --split, so this guards the manifest check behind it."""
    module = _load_wrapper()
    manifest = json.loads((demo["data"] / "manifest.json").read_text())
    del manifest["splits"]["val"]
    (demo["data"] / "manifest.json").write_text(json.dumps(manifest))
    args = module.build_parser().parse_args(
        [
            "--base-dir",
            str(demo["base"]),
            "--data-dir",
            str(demo["data"]),
            "--input-dir",
            str(demo["inputs"]),
            "--checkpoint",
            str(demo["checkpoint"]),
            "--output-dir",
            str(demo["out"]),
            "--mode",
            "evaluate",
            "--split",
            "val",
        ]
    )
    with pytest.raises(module.JobError, match="not in the prepared manifest"):
        module.preflight(args, [])


# --- infer -----------------------------------------------------------------


def test_infer_is_the_default_mode(demo, monkeypatch):
    assert run(demo, monkeypatch) == 0
    assert results_of(demo)["mode"] == "infer"


def test_infer_argv_matches_the_demos_own_launcher(demo, monkeypatch):
    assert run(demo, monkeypatch) == 0
    argv = recorded(demo, "infer")
    # Physical-consistency repair and mixed precision are on, as upstream's own
    # slurm launcher has them.
    assert "--amp" in argv
    assert "--enforce-temperature-order" in argv
    assert argv[argv.index("--format") + 1] == "netcdf"
    # All three variables, together, by absolute path.
    inputs = [argv[i + 1] for i, a in enumerate(argv) if a == "--input"]
    assert {i.split("=")[0] for i in inputs} == {"tmin", "tmax", "prcp"}
    assert all(Path(i.split("=", 1)[1]).is_absolute() for i in inputs)


def test_infer_records_the_netcdf_as_a_sized_artifact_not_a_figure(demo, monkeypatch):
    """Bulk output is reported, never promoted into the fetchable set."""
    assert run(demo, monkeypatch) == 0
    results = results_of(demo)
    netcdf = [a for a in results["artifacts"] if a["kind"] == "netcdf"]
    assert len(netcdf) == 1
    assert netcdf[0]["bytes"] == 4096
    assert not any(a["path"].endswith(".nc") for a in results["figures"])


def test_infer_results_carry_the_pipelines_own_run_block(demo, monkeypatch):
    assert run(demo, monkeypatch, "--days", "2") == 0
    run_block = results_of(demo)["run"]
    assert run_block["output_timesteps"] == 2
    assert run_block["variables"] == ["tmin", "tmax", "prcp"]
    assert run_block["temperature_order_enforced"] is True


# --- evaluate --------------------------------------------------------------


def test_evaluate_discards_predictions_unless_plots_need_them(demo, monkeypatch):
    assert run(demo, monkeypatch, "--mode", "evaluate") == 0
    assert "--no-save-predictions" in recorded(demo, "evaluate")
    assert not any(a["kind"] == "predictions" for a in results_of(demo)["artifacts"])


def test_evaluate_with_plots_retains_predictions_and_runs_both_pipelines(
    demo, monkeypatch
):
    """
    The prepared index is `netcdf_patch_index`, for which the evaluation pipeline's
    own inline plotting never fires — the statistics must come from the standalone
    utility, which reads predictions.npy.
    """
    assert run(demo, monkeypatch, "--mode", "evaluate", "--plots") == 0
    assert "--no-save-predictions" not in recorded(demo, "evaluate")
    assert recorded(demo, "spatial") == ["test", "21"]
    results = results_of(demo)
    assert len(results["provenance"]["commands"]) == 2
    assert results["spatial_statistics"]["statistics"] == ["mean_tmin", "p95_prcp"]
    assert any("mean_tmin_comparison.png" in f for f in results["figures"])
    assert any(a["kind"] == "predictions" for a in results["artifacts"])


def test_evaluate_metrics_carry_model_and_baseline_with_units(demo, monkeypatch):
    assert run(demo, monkeypatch, "--mode", "evaluate") == 0
    results = results_of(demo)
    tmin = results["metrics"]["tmin"]
    assert set(tmin["model"]) == {"bias", "mae", "rmse"}
    assert set(tmin["bilinear_baseline"]) == {"bias", "mae", "rmse"}
    assert tmin["mae_improvement_percent"] == pytest.approx(75.37)
    assert tmin["count"] == 1545894720
    units = results["run"]["variable_metadata"]
    assert units["tmin"]["units"] == "degC"
    assert units["prcp"]["units"] == "mm/day"


def test_evaluate_reports_the_physical_consistency_fraction(demo, monkeypatch):
    """The share of cells where the raw model put tmin above tmax."""
    assert run(demo, monkeypatch, "--mode", "evaluate") == 0
    assert results_of(demo)["run"][
        "temperature_order_violation_fraction"
    ] == pytest.approx(0.0142)


# --- upstream failure ------------------------------------------------------


def test_a_pipeline_that_writes_no_json_fails_instead_of_emitting_nulls(
    demo, monkeypatch, capsys
):
    """The whole point of composing from upstream JSON: absence is a failure."""
    (demo["base"] / "pipeline_04_infer.py").write_text("print('ran, wrote nothing')\n")
    assert run(demo, monkeypatch) == 1
    assert not (demo["out"] / "results.json").exists()
    assert "did not write its expected output" in capsys.readouterr().err


def test_unparseable_upstream_json_fails_loudly(demo, monkeypatch, capsys):
    (demo["base"] / "pipeline_04_infer.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "out = sys.argv[sys.argv.index('--output') + 1]\n"
        "Path(out).write_bytes(b'')\n"
        "Path(out + '.json').write_text('{not json')\n"
    )
    assert run(demo, monkeypatch) == 1
    assert "not valid JSON" in capsys.readouterr().err
    assert not (demo["out"] / "results.json").exists()


def test_a_nonzero_pipeline_exit_is_reported_with_its_command(
    demo, monkeypatch, capsys
):
    (demo["base"] / "pipeline_04_infer.py").write_text("import sys; sys.exit(3)\n")
    assert run(demo, monkeypatch) == 1
    error = capsys.readouterr().err
    assert "pipeline exited 3" in error
    assert "pipeline_04_infer.py" in error


def test_the_run_log_is_always_written(demo, monkeypatch):
    assert run(demo, monkeypatch) == 0
    log = (demo["out"] / "refine_stdout.txt").read_text()
    assert "pipeline_04_infer.py" in log


# --- provenance ------------------------------------------------------------


def test_provenance_pins_what_a_reader_needs_to_reproduce(demo, monkeypatch):
    monkeypatch.setenv("SLURM_JOB_ID", "4408123")
    assert run(demo, monkeypatch) == 0
    provenance = results_of(demo)["provenance"]
    assert provenance["slurm_job_id"] == "4408123"
    assert provenance["checkpoint"] == str(demo["checkpoint"])
    assert provenance["env_prefix"] == str(demo["env"])
    assert provenance["base_dir"] == str(demo["base"])
    assert isinstance(provenance["wall_seconds"], float)
    assert provenance["commands"], "the exact pipeline invocations"
    json.dumps(results_of(demo))  # written to disk verbatim


def test_input_checksums_are_opt_in(demo, monkeypatch):
    assert run(demo, monkeypatch) == 0
    assert results_of(demo)["provenance"]["input_sha256"] == {}

    assert run(demo, monkeypatch, "--checksum-inputs") == 0
    digests = results_of(demo)["provenance"]["input_sha256"]
    assert set(digests) == {"tmin", "tmax", "prcp"}


def test_a_cpu_fallback_is_warned_about_rather_than_passing_silently(demo, monkeypatch):
    """
    A GPU job that quietly ran on CPU produces valid numbers and meaningless
    timings; provenance must say which happened (task 1.4, wrapper half).
    """
    assert run(demo, monkeypatch) == 0
    provenance = results_of(demo)["provenance"]
    if provenance.get("cuda_available") is False:
        assert any("no GPU" in w for w in results_of(demo)["warnings"])
    else:  # pragma: no cover - only on a GPU host
        assert "gpu" in provenance


# --- figures ---------------------------------------------------------------


def test_evaluate_without_plots_still_produces_a_figure(demo, monkeypatch):
    pytest.importorskip("matplotlib")
    assert run(demo, monkeypatch, "--mode", "evaluate") == 0
    results = results_of(demo)
    assert results["figures"] == ["metrics_comparison.png"]
    assert (demo["out"] / "metrics_comparison.png").read_bytes()[:4] == b"\x89PNG"


def test_infer_draws_one_quicklook_per_variable(demo, monkeypatch):
    """netCDF4 is not in this venv, so the reader is shimmed — the plotting code
    under test is ours."""
    pytest.importorskip("matplotlib")
    numpy = pytest.importorskip("numpy")

    class _Var:
        def __init__(self, array):
            self._array = array

        def __getitem__(self, index):
            return self._array[index]

    class _Dataset:
        def __init__(self, path, *_a, **_k):
            fine = "inference" in str(path)
            shape = (2, 24, 48) if fine else (2, 4, 8)
            names = ("tmin", "tmax", "prcp") if fine else ("tmin", "tmax", "pr")
            generator = numpy.random.default_rng(0)
            self.variables = {n: _Var(generator.normal(size=shape)) for n in names}

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    module = type(sys)("netCDF4")
    module.Dataset = _Dataset
    monkeypatch.setitem(sys.modules, "netCDF4", module)

    assert run(demo, monkeypatch) == 0
    results = results_of(demo)
    assert results["figures"] == [
        "quicklook_tmin.png",
        "quicklook_tmax.png",
        "quicklook_prcp.png",
    ]
    for figure in results["figures"]:
        assert (demo["out"] / figure).read_bytes()[:4] == b"\x89PNG"


def test_a_figure_failure_never_discards_a_successful_run(demo, monkeypatch):
    """
    The GPU work is already done by the time anything is drawn. A plotting problem
    is a warning, not a reason to throw the run away.
    """
    module = type(sys)("netCDF4")

    def _explode(*_a, **_k):
        raise RuntimeError("no reader here")

    module.Dataset = _explode
    monkeypatch.setitem(sys.modules, "netCDF4", module)

    assert run(demo, monkeypatch) == 0
    results = results_of(demo)
    assert results["status"] == "ok"
    assert results["figures"] == []
    assert any("quicklook" in w for w in results["warnings"])


# --- the real JobSpec ------------------------------------------------------
#
# Everything above tests the job's own files. These test what vista actually
# hands IRI for this job, with IRI and Globus faked — the last thing that can be
# wrong before a real Frontier submission, and the cheapest place to catch it.


FRONTIER_REMOTE_DIR = "/fake/frontier/vista"


@pytest.fixture
def _frontier_submit(monkeypatch):
    """
    Patch IRI/Globus the way tests/test_submit_job_spec.py does.

    Neither half of "where and as whom" is a deployment constant any more: the
    OLCF project comes from the researcher's S3M token, and the folder from
    their own `frontier_remote_dir` setting. So both are faked on the call
    rather than monkeypatched onto `settings`.
    """
    import vista_mcp_server.submit_job_mcp as submit_job_mcp
    from fakes import FakeGlobusClient, FakeIriClient
    from vista_mcp_server.config import settings
    from vista_mcp_server.lib.user_config import UserConfig
    from vista_mcp_server.submit_job_mcp import _submit_frontier_job

    monkeypatch.setattr(settings, "local_hpc_jobs_dir", REPO_ROOT / "hpc_jobs")
    monkeypatch.setattr(
        settings, "frontier_globus_collection_id", "frontier-collection"
    )
    iri = FakeIriClient(job_id="fr-refine-1")
    globus = FakeGlobusClient()
    globus.seed_remote_dir(FRONTIER_REMOTE_DIR)

    async def _olcf(*, iri_token: str):
        return iri

    async def _introspect(token, *, introspect_url):
        return "abc123"

    monkeypatch.setattr(submit_job_mcp, "create_olcf_iri_client", _olcf)
    monkeypatch.setattr(submit_job_mcp, "create_globus_client", lambda **kw: globus)
    monkeypatch.setattr(submit_job_mcp, "get_s3m_token_project", _introspect)

    async def submit(script_args=None):
        return await _submit_frontier_job(
            UserConfig(
                frontier_s3m_token="frontier-token",
                frontier_remote_dir=FRONTIER_REMOTE_DIR,
                globus_token="fake-transfer",
                globus_https_token="fake-https",
            ),
            "refine-downscaling",
            node_count=None,
            duration_int=None,
            script_args=script_args,
        )

    return submit, iri, globus


@pytest.mark.anyio
async def test_the_jobspec_asks_for_one_exclusive_gpu_node_on_the_shared_account(
    _frontier_submit,
):
    submit, iri, _ = _frontier_submit
    job_id, _log, _err, _out, nodes, duration = await submit()

    assert job_id == "fr-refine-1"
    assert (nodes, duration) == (1, 1800)
    spec, _ = iri.submitted[0]
    assert spec["resources"]["node_count"] == 1
    assert spec["resources"]["process_count"] == 1
    assert spec["resources"]["exclusive_node_use"] is True
    attributes = spec["attributes"]
    assert attributes["queue_name"] == "batch"
    # Charged to the project the researcher's own token names, never the demo's
    # cli138 — and no longer a deployment-wide account.
    assert attributes["account"] == "abc123"


@pytest.mark.anyio
async def test_the_jobspec_carries_every_staged_path_into_the_job_environment(
    _frontier_submit,
):
    submit, iri, _ = _frontier_submit
    await submit()
    environment = iri.submitted[0][0]["attributes"]["environment"]

    assert environment["RUN_DIR_Frontier"].endswith("/refine-downscaling/src")
    for key in (
        "REFINE_BASE_DIR",
        "REFINE_ENV",
        "REFINE_CHECKPOINT",
        "REFINE_DATA_DIR",
        "REFINE_INPUT_DIR",
    ):
        assert environment[key].startswith("/lustre/orion/"), key


@pytest.mark.anyio
async def test_the_job_script_is_inlined_verbatim_after_the_vista_preamble(
    _frontier_submit,
):
    submit, iri, _ = _frontier_submit
    await submit()
    job_cmd = iri.submitted[0][0]["arguments"][2]

    assert (JOB_DIR / "job.frontier.slurm").read_text() in job_cmd
    # The dispatcher's preamble has to land before our script, since the script
    # reads VISTA_OUT and relies on the purge.
    assert job_cmd.index("VISTA_OUT=") < job_cmd.index("#!/bin/bash -l")
    assert "module purge" in job_cmd


@pytest.mark.anyio
async def test_script_args_reach_the_script_as_positional_parameters(
    _frontier_submit,
):
    """
    The wrapper is invoked with `"$@"`, so script_args only work if the dispatcher
    sets them with `set --` ahead of the inlined script.
    """
    submit, iri, _ = _frontier_submit
    await submit("--mode evaluate --max-days 3 --plots")
    job_cmd = iri.submitted[0][0]["arguments"][2]

    assert "set -- --mode evaluate --max-days 3 --plots" in job_cmd
    assert job_cmd.index("set --") < job_cmd.index("#!/bin/bash -l")


@pytest.mark.anyio
async def test_no_script_args_means_no_positional_parameters_at_all(
    _frontier_submit,
):
    """
    With nothing to forward the dispatcher emits no `set --` line at all, so the
    script's `"$@"` expands to nothing and the wrapper applies its own defaults.
    (Bash special-cases an unset `$@` under `set -u`, so this is safe either way —
    the point is that the default submission carries no arguments.)
    """
    submit, iri, _ = _frontier_submit
    await submit(None)
    assert "set --" not in iri.submitted[0][0]["arguments"][2]


@pytest.mark.anyio
async def test_the_wrapper_is_staged_to_the_run_dir(_frontier_submit):
    """`job.frontier.slurm` runs $RUN_DIR_Frontier/run_downscaling.py, so it has
    to have been shipped there."""
    submit, _iri, globus = _frontier_submit
    await submit()
    transferred = " ".join(remote for _collection, remote in globus.uploads)
    assert "run_downscaling.py" in transferred
