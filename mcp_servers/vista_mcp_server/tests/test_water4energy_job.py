"""
Contract tests for the `water4energy-diagnostic` HPC job.

Covers the two halves vista owns: the Frontier submission defaults in
`cluster_defaults.json`, and `run_diagnostic.py` — the wrapper that turns the
skill's `script_args` into an upstream command line, preflights the pre-staged
inputs, and turns the printed metric summary into `results.json`.

Hermetic: the upstream `plot_e3sm_era5.py` is stubbed, so nothing here needs
Frontier, Globus, the 1.25 GB of climatologies, or the scientific Python stack.
"""

from __future__ import annotations

import importlib.util
import json
import textwrap
from pathlib import Path

import pytest

from vista_mcp_server.submit_job_mcp import ClusterDefaults

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
JOB_DIR = REPO_ROOT / "hpc_jobs" / "water4energy-diagnostic"

# The exact summary the upstream program prints, from the verified Frontier run
# (FRONTIER_TEST_REPORT.md). This is the format `parse_metrics_stdout` is pinned to.
FRONTIER_STDOUT = textwrap.dedent("""\
    Surface temperature:
      global: r=0.9937, RMSE=1.685 °C, bias=+0.324 °C
      TVA:    r=0.9519, RMSE=0.565 °C, bias=-0.371 °C
    Precipitation:
      global: r=0.8879, RMSE=1.047 mm/day, bias=+0.083 mm/day
      TVA:    r=0.5299, RMSE=0.250 mm/day, bias=+0.215 mm/day
    Figures written to: /somewhere/plots
    """)


def _load_wrapper():
    path = JOB_DIR / "run_diagnostic.py"
    spec = importlib.util.spec_from_file_location("w4e_run_diagnostic", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- cluster_defaults ------------------------------------------------------


def test_cluster_defaults_are_frontier_only_and_short():
    defaults = ClusterDefaults.model_validate_json(
        (JOB_DIR / "cluster_defaults.json").read_text()
    )
    assert defaults.odo is None and defaults.perlmutter is None
    frontier = defaults.frontier
    assert frontier is not None
    # A ~40 s CPU diagnostic: one node, one rank, ten minutes.
    assert frontier.duration == 600
    assert frontier.resources.node_count == 1
    assert frontier.resources.processes_per_node is None, (
        "must stay 1-rank — job.frontier.slurm deliberately has no inner srun"
    )
    # Frontier's `debug` is a QOS, not a partition, and IriAttributes has no qos
    # field, so this job runs in `batch` (openspec design decision 3).
    assert frontier.iri.queue_name == "batch"


def test_cluster_defaults_declare_the_job_environment():
    defaults = ClusterDefaults.model_validate_json(
        (JOB_DIR / "cluster_defaults.json").read_text()
    )
    env = defaults.frontier.iri.environment
    assert set(env) == {
        "W4E_DATA_DIR",
        "W4E_ENV",
        "W4E_REPO_URL",
        "W4E_REPO_REF",
        "W4E_PYTHON_MODULE",
    }
    # The climatologies are pre-staged read-only, not produced by the job.
    assert env["W4E_DATA_DIR"].startswith("/lustre/")
    assert env["W4E_REPO_URL"].startswith("https://")


def test_frontier_script_declares_no_sbatch_directives():
    """
    Vista submits an IRI JobSpec with this script inlined as the body, so #SBATCH
    lines would be inert comments. Keeping them out stops anyone from believing
    the queue or walltime is configured here.
    """
    script = (JOB_DIR / "job.frontier.slurm").read_text()
    directives = [
        line for line in script.splitlines() if line.lstrip().startswith("#SBATCH")
    ]
    assert not directives, f"unexpected #SBATCH directives: {directives}"
    # It should still *explain* why, so the next maintainer does not add them back.
    assert "#SBATCH" in script, "keep the comment explaining why there are none"


# --- script_args -> upstream argv -----------------------------------------


def _argv(tmp_path, *script_args):
    w4e = _load_wrapper()
    args = w4e.parse_args(
        [
            "--skill-root",
            str(tmp_path / "clone"),
            "--data-dir",
            str(tmp_path / "data"),
            "--output-dir",
            str(tmp_path / "out"),
            *script_args,
        ]
    )
    return (
        w4e,
        args,
        w4e.build_upstream_argv(
            args,
            python="/venv/bin/python",
            script=tmp_path / "clone" / "plot_e3sm_era5.py",
            inputs={
                "era5": Path("/data/era5.nc"),
                "e3sm": Path("/data/e3sm.nc"),
                "tva_boundary": Path("/clone/tva.geojson"),
            },
            plots_dir=tmp_path / "out" / "plots",
            cartopy_data=tmp_path / "clone" / "cartopy_data",
        ),
    )


def test_default_argv_omits_tunables_so_upstream_defaults_apply(tmp_path):
    _, _, argv = _argv(tmp_path)
    assert "--resolution" not in argv and "--dpi" not in argv
    # The inputs and the offline coastline cache are always passed explicitly.
    assert argv[argv.index("--era5") + 1] == "/data/era5.nc"
    assert argv[argv.index("--e3sm") + 1] == "/data/e3sm.nc"
    assert argv[argv.index("--tva-boundary") + 1] == "/clone/tva.geojson"
    assert "--cartopy-data" in argv


def test_script_args_are_forwarded(tmp_path):
    _, _, argv = _argv(tmp_path, "--resolution", "0.5", "--dpi", "150")
    assert argv[argv.index("--resolution") + 1] == "0.5"
    assert argv[argv.index("--dpi") + 1] == "150"


def test_cartopy_data_flag_is_dropped_when_the_cache_is_absent(tmp_path):
    """Without the bundled coastline the run still proceeds; cartopy may fetch."""
    w4e = _load_wrapper()
    args = w4e.parse_args(
        ["--skill-root", "c", "--data-dir", "d", "--output-dir", str(tmp_path)]
    )
    argv = w4e.build_upstream_argv(
        args,
        python="py",
        script=Path("p.py"),
        inputs={"era5": Path("/a"), "e3sm": Path("/b"), "tva_boundary": Path("/c")},
        plots_dir=tmp_path,
        cartopy_data=None,
    )
    assert "--cartopy-data" not in argv


def test_bare_input_names_resolve_to_absolute_paths(tmp_path):
    """
    Upstream runs with cwd=clone, so a relative input path would resolve inside the
    clone and be reported missing. Resolution must always yield an absolute path.
    """
    w4e = _load_wrapper()
    data, clone = tmp_path / "data", tmp_path / "clone"
    data.mkdir()
    clone.mkdir()
    (data / "era5.nc").write_bytes(b"x")
    (clone / "tva.geojson").write_text("{}")

    staged = w4e.resolve_input("era5.nc", data, clone)
    in_clone = w4e.resolve_input("tva.geojson", data, clone)
    missing = w4e.resolve_input("absent.nc", data, clone)

    assert staged == (data / "era5.nc").resolve()  # data dir wins
    assert in_clone == (clone / "tva.geojson").resolve()  # falls back to the clone
    assert missing.is_absolute() and missing.parent == data.resolve()


# --- preflight -------------------------------------------------------------


def test_preflight_names_the_data_dir_when_it_is_unreadable(tmp_path):
    w4e = _load_wrapper()
    missing_dir = tmp_path / "gone"
    with pytest.raises(SystemExit) as exc:
        w4e.preflight({"era5": missing_dir / "era5.nc"}, missing_dir)
    message = str(exc.value)
    assert str(missing_dir) in message
    assert "W4E_DATA_DIR" in message, "must name the knob a human has to change"


def test_preflight_names_every_missing_input(tmp_path):
    w4e = _load_wrapper()
    data = tmp_path / "data"
    data.mkdir()
    (data / "era5.nc").write_bytes(b"x")
    with pytest.raises(SystemExit) as exc:
        w4e.preflight({"era5": data / "era5.nc", "e3sm": data / "e3sm.nc"}, data)
    message = str(exc.value)
    assert "e3sm.nc" in message
    assert "era5.nc" not in message.split("Searched")[0], (
        "present input must not be listed"
    )


def test_preflight_passes_when_every_input_exists(tmp_path):
    w4e = _load_wrapper()
    data = tmp_path / "data"
    data.mkdir()
    for name in ("era5.nc", "e3sm.nc"):
        (data / name).write_bytes(b"x")
    w4e.preflight({"era5": data / "era5.nc", "e3sm": data / "e3sm.nc"}, data)


# --- stdout -> metrics -----------------------------------------------------


def test_frontier_summary_parses_to_the_published_reference_values():
    w4e = _load_wrapper()
    variables = w4e.parse_metrics_stdout(FRONTIER_STDOUT)

    assert set(variables) == {"surface_temperature", "precipitation"}
    assert variables["surface_temperature"]["units"] == "degC"
    assert variables["precipitation"]["units"] == "mm/day"

    temp = variables["surface_temperature"]
    assert temp["global"] == {"correlation": 0.9937, "rmse": 1.685, "bias": 0.324}
    assert temp["region"] == {"correlation": 0.9519, "rmse": 0.565, "bias": -0.371}

    precip = variables["precipitation"]
    assert precip["global"] == {"correlation": 0.8879, "rmse": 1.047, "bias": 0.083}
    assert precip["region"] == {"correlation": 0.5299, "rmse": 0.25, "bias": 0.215}


@pytest.mark.parametrize(
    "mangled, missing",
    [
        # precipitation block dropped entirely
        (FRONTIER_STDOUT.split("Precipitation:")[0], "precipitation"),
        # the regional row dropped from both blocks
        (
            "\n".join(
                line for line in FRONTIER_STDOUT.splitlines() if "TVA:" not in line
            ),
            "region",
        ),
        ("", "surface_temperature"),
    ],
)
def test_a_partial_summary_fails_loudly(mangled, missing):
    """
    A half-parsed metrics block would be reported as science. Failing names what is
    missing so the format drift gets fixed instead of silently emitting nulls.
    """
    w4e = _load_wrapper()
    with pytest.raises(SystemExit) as exc:
        w4e.parse_metrics_stdout(mangled)
    assert missing in str(exc.value)


def test_results_json_carries_metrics_and_provenance(tmp_path, monkeypatch):
    w4e = _load_wrapper()
    monkeypatch.setenv("W4E_REPO_URL", "https://example.invalid/repo")
    monkeypatch.setenv("W4E_REPO_REF", "main")
    monkeypatch.setenv("SLURM_JOB_ID", "4408123")

    era5 = tmp_path / "era5.nc"
    era5.write_bytes(b"0123456789")
    figures = [tmp_path / "surface_temperature_comparison.png"]

    results = w4e.build_results(
        stdout_text=FRONTIER_STDOUT,
        inputs={"era5": era5},
        figures=figures,
        skill_root=tmp_path,
        resolution=None,
        wall_seconds=41.23,
        checksum_inputs=False,
    )

    assert results["schema"] == "vista/water4energy-diagnostic/results/v1"
    assert results["status"] == "ok"
    assert results["metrics_source"] == "stdout"
    assert results["region_name"] == "TVA"
    # No --resolution passed through -> record the upstream default, not null.
    assert results["grid"] == {"resolution_deg": 1.0}
    assert results["figures"] == ["surface_temperature_comparison.png"]

    prov = results["provenance"]
    assert prov["repo"]["url"] == "https://example.invalid/repo"
    assert prov["repo"]["ref"] == "main"
    assert prov["slurm_job_id"] == "4408123"
    assert prov["wall_seconds"] == 41.2
    assert prov["inputs"]["era5"]["bytes"] == 10
    assert "sha256" not in prov["inputs"]["era5"], "checksums are opt-in"
    assert set(prov["packages"]) >= {"cartopy", "xarray", "netCDF4"}
    # Serializable — it is written to disk verbatim.
    json.dumps(results)


def test_checksum_inputs_adds_a_digest(tmp_path):
    w4e = _load_wrapper()
    era5 = tmp_path / "era5.nc"
    era5.write_bytes(b"water4energy")
    results = w4e.build_results(
        stdout_text=FRONTIER_STDOUT,
        inputs={"era5": era5},
        figures=[],
        skill_root=tmp_path,
        resolution=1.0,
        wall_seconds=1.0,
        checksum_inputs=True,
    )
    assert results["provenance"]["inputs"]["era5"]["sha256"] == w4e.sha256(era5)


# --- figure collection ----------------------------------------------------


def test_figures_are_copied_to_the_output_root(tmp_path):
    """So callers can fetch by name without knowing about the plots subdir."""
    w4e = _load_wrapper()
    plots, out = tmp_path / "out" / "plots", tmp_path / "out"
    plots.mkdir(parents=True)
    for stem in w4e.FIGURE_STEMS:
        for suffix in w4e.FIGURE_SUFFIXES:
            (plots / f"{stem}{suffix}").write_bytes(b"x" * 64)

    collected = w4e.collect_figures(plots, out)
    assert len(collected) == 4
    for stem in w4e.FIGURE_STEMS:
        assert (out / f"{stem}.png").is_file()


@pytest.mark.parametrize("payload", [b"", None])
def test_empty_or_absent_figure_is_an_error(tmp_path, payload):
    """A zero-exit run that produced no usable figure must not read as success."""
    w4e = _load_wrapper()
    plots, out = tmp_path / "out" / "plots", tmp_path / "out"
    plots.mkdir(parents=True)
    for stem in w4e.FIGURE_STEMS:
        for suffix in w4e.FIGURE_SUFFIXES:
            target = plots / f"{stem}{suffix}"
            if payload is None:
                continue
            target.write_bytes(payload)

    with pytest.raises(SystemExit, match="missing or empty"):
        w4e.collect_figures(plots, out)
