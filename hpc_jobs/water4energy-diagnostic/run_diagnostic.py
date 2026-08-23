#!/usr/bin/env python3
"""
Vista HPC wrapper for the ``water4energy-diagnostic`` job.

Runs the upstream ERA5 vs E3SMv3 annual-climatology comparison
(``plot_e3sm_era5.py`` from the cloned water4energy_diagnostic repo) against
climatology files that are pre-staged read-only on Lustre, lands the four figures
at the top level of the job's output dir, and writes a machine-readable
``results.json`` beside them.

This file is the only non-metadata file in ``hpc_jobs/water4energy-diagnostic/``, so
vista Globus-stages it to ``$RUN_DIR_Frontier``. ``job.frontier.slurm`` invokes it as::

    python run_diagnostic.py --skill-root <clone> --data-dir $W4E_DATA_DIR \
        --output-dir $VISTA_OUT <passthrough...>

The passthrough flags are the skill's ``script_args`` contract (see the job README).
The upstream program is invoked unmodified, as a subprocess, with ``cwd`` set to the
clone root — vista owns no copy of the analysis pipeline.

Metrics are read from the printed summary, which carries correlation, RMSE, and bias
per variable per scope. The means, percent-normalized metrics, and cell counts are
computed upstream but only ever rendered into the figures' table panel, so they are
not machine-readable — see the job README. The upstream program is deliberately left
unmodified; ``results.json`` records ``metrics_source`` so a reader knows the numbers
were parsed from output rather than recomputed here.

Inputs are opened read-only and never written. Everything this job writes goes under
``--output-dir``.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

UPSTREAM_SCRIPT = "plot_e3sm_era5.py"

RESULTS_SCHEMA = "vista/water4energy-diagnostic/results/v1"

# The three inputs the upstream diagnostic needs, under the names it expects.
# The two NetCDFs are gitignored upstream and live in --data-dir; the TVA polygon
# ships inside the repo, so it resolves against the clone.
ERA5_DEFAULT = "ERA5_ANN_198501_201412_climo.nc"
E3SM_DEFAULT = "v3.LR.historical_0101_ANN_198501_201412_climo.nc"
TVA_DEFAULT = "tva_power_service_area.geojson"

# What plot_e3sm_era5.py writes, per variable, into its --output-dir.
FIGURE_STEMS = ("surface_temperature_comparison", "precipitation_comparison")
FIGURE_SUFFIXES = (".png", ".pdf")

# Written next to the figures so the run's own log survives with its artifacts.
STDOUT_ARTIFACT = "diagnostic_stdout.txt"
RESULTS_ARTIFACT = "results.json"

# Subdir under --output-dir that the upstream program writes into. Kept separate
# from the output-dir root so cartopy's `.cartopy` scratch dir and the PDFs don't
# clutter the files a user fetches by name.
PLOTS_SUBDIR = "plots"

# Pinned by the upstream requirements.txt; reported for provenance.
TRACKED_PACKAGES = (
    "cartopy",
    "matplotlib",
    "netCDF4",
    "numpy",
    "scipy",
    "shapely",
    "xarray",
)

# The upstream summary lines, e.g.
#   Surface temperature:
#     global: r=0.9937, RMSE=1.685 °C, bias=+0.324 °C
#     TVA:    r=0.9519, RMSE=0.565 °C, bias=-0.371 °C
_SECTION_RE = re.compile(r"^(?P<label>Surface temperature|Precipitation):\s*$")
_METRIC_RE = re.compile(
    r"^\s+(?P<scope>global|TVA):\s+"
    r"r=(?P<r>[-+]?[\d.]+),\s+"
    r"RMSE=(?P<rmse>[-+]?[\d.]+)\s+(?P<units>\S+),\s+"
    r"bias=(?P<bias>[-+]?[\d.]+)"
)
# Upstream's own variable keys, so both metric paths produce the same shape.
_LABEL_TO_KEY = {
    "Surface temperature": "surface_temperature",
    "Precipitation": "precipitation",
}
_SCOPE_TO_KEY = {"global": "global", "TVA": "region"}

# The region the shipped polygon describes, and the units the program reports in.
REGION_NAME = "TVA"
UNITS = {"surface_temperature": "degC", "precipitation": "mm/day"}


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run the water4energy ERA5/E3SM climatology diagnostic."
    )
    p.add_argument(
        "--skill-root",
        required=True,
        help="Path to the cloned water4energy_diagnostic repo.",
    )
    p.add_argument(
        "--data-dir",
        required=True,
        help="Read-only dir holding the pre-staged NetCDF climatologies ($W4E_DATA_DIR).",
    )
    p.add_argument(
        "--output-dir",
        required=True,
        help="Where figures, results.json, and logs land (usually $VISTA_OUT).",
    )

    # --- script_args passthrough (the skill's contract) ---------------------
    p.add_argument(
        "--resolution",
        type=float,
        default=None,
        help="Comparison-grid spacing in degrees (upstream default 1.0).",
    )
    p.add_argument(
        "--dpi", type=int, default=None, help="PNG resolution (upstream default 200)."
    )
    p.add_argument(
        "--checksum-inputs",
        action="store_true",
        help="SHA256 the input climatologies into provenance. Off by default: it "
        "reads 1.25 GB off Lustre and the diagnostic itself only takes ~40 s.",
    )
    # Input overrides. Each accepts an absolute path, or a bare filename resolved
    # against --data-dir then --skill-root. Present so a different climatology pair
    # or region polygon needs no code change; the defaults are the validated set.
    p.add_argument("--era5", default=ERA5_DEFAULT, help="ERA5 climatology NetCDF.")
    p.add_argument("--e3sm", default=E3SM_DEFAULT, help="E3SMv3 climatology NetCDF.")
    p.add_argument(
        "--tva-boundary", default=TVA_DEFAULT, help="Region polygon (GeoJSON)."
    )
    return p.parse_args(argv)


def resolve_input(value: str, data_dir: Path, skill_root: Path) -> Path:
    """
    Resolve one input. Absolute paths are taken as given; a bare name is looked
    for in --data-dir first (so a staged file wins) and then in the clone (where
    the region polygon ships).

    The result is always absolute: the upstream program runs with cwd set to the
    clone root, so a relative path would resolve against the clone and be
    reported as missing.
    """
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    for base in (data_dir, skill_root):
        resolved = (base / candidate).resolve()
        if resolved.is_file():
            return resolved
    # Nothing found — return the data-dir location so preflight reports the path a
    # user would have to fix, rather than a bare filename.
    return (data_dir / candidate).resolve()


def preflight(inputs: dict[str, Path], data_dir: Path) -> None:
    """
    Fail before any work with a message that names what is missing and why, rather
    than letting xarray or cartopy raise 200 lines deep.

    The two NetCDF climatologies are pre-staged on Lustre and are NOT part of the
    repo, so a missing input here almost always means the data dir moved or its
    group read permission changed for the account vista submits under.
    """
    if not data_dir.is_dir():
        raise SystemExit(
            f"[water4energy] ERROR: data dir not readable: {data_dir}\n"
            f"[water4energy] This is $W4E_DATA_DIR from cluster_defaults.json. It must be a\n"
            f"[water4energy] directory readable by the account vista submits Frontier jobs under.\n"
            f"[water4energy] Fix the path in hpc_jobs/water4energy-diagnostic/cluster_defaults.json,\n"
            f"[water4energy] or restore group read access on the staged climatologies."
        )

    missing = {name: path for name, path in inputs.items() if not path.is_file()}
    if missing:
        lines = [
            "[water4energy] ERROR: missing required input(s):",
            *(f"[water4energy]   {name}: {path}" for name, path in missing.items()),
            f"[water4energy] Searched $W4E_DATA_DIR ({data_dir}) and the cloned repo.",
            "[water4energy] The two NetCDF climatologies are gitignored upstream and must be",
            "[water4energy] pre-staged on Lustre; only the region polygon ships in the repo.",
        ]
        raise SystemExit("\n".join(lines))

    for name, path in inputs.items():
        print(f"[water4energy] input {name}: {path} ({path.stat().st_size} bytes)")


def build_upstream_argv(
    args: argparse.Namespace,
    *,
    python: str,
    script: Path,
    inputs: dict[str, Path],
    plots_dir: Path,
    cartopy_data: Path | None,
) -> list[str]:
    """Assemble the plot_e3sm_era5.py command line. Only explicit flags are passed."""
    argv = [
        python,
        "-u",
        str(script),
        "--era5",
        str(inputs["era5"]),
        "--e3sm",
        str(inputs["e3sm"]),
        "--tva-boundary",
        str(inputs["tva_boundary"]),
        "--output-dir",
        str(plots_dir),
    ]
    if cartopy_data is not None:
        # Keeps the run offline: the bundled Natural Earth 110 m coastline means
        # cartopy never reaches for a download on the compute node.
        argv += ["--cartopy-data", str(cartopy_data)]
    if args.resolution is not None:
        argv += ["--resolution", str(args.resolution)]
    if args.dpi is not None:
        argv += ["--dpi", str(args.dpi)]
    return argv


def parse_metrics_stdout(text: str) -> dict[str, dict]:
    """
    Read the metrics out of the diagnostic's printed summary.

    Returns {variable: {scope: {correlation, rmse, bias}}}. That is everything the
    program prints; its means, percent-normalized metrics, and cell counts reach
    the figures' table panel only.

    Raises on a partial parse. A silently-empty or half-filled metrics block would
    be reported as science; a hard failure gets the format drift fixed.
    """
    variables: dict[str, dict] = {}
    label = None
    for line in text.splitlines():
        section = _SECTION_RE.match(line)
        if section:
            label = section.group("label")
            continue
        metric = _METRIC_RE.match(line)
        if not metric or label is None:
            continue
        entry = variables.setdefault(
            _LABEL_TO_KEY[label], {"units": UNITS[_LABEL_TO_KEY[label]]}
        )
        entry[_SCOPE_TO_KEY[metric.group("scope")]] = {
            "correlation": float(metric.group("r")),
            "rmse": float(metric.group("rmse")),
            "bias": float(metric.group("bias")),
        }

    expected = {"surface_temperature": ("global", "region"), "precipitation": ("global", "region")}
    problems = [
        f"{var}.{scope}"
        for var, scopes in expected.items()
        for scope in scopes
        if scope not in variables.get(var, {})
    ]
    if problems:
        raise SystemExit(
            "[water4energy] ERROR: could not read the metric summary from the "
            "diagnostic's output.\n"
            f"[water4energy] Missing: {', '.join(problems)}\n"
            "[water4energy] The upstream print format has probably changed; update "
            "parse_metrics_stdout in\n"
            f"[water4energy] run_diagnostic.py. Raw output is in {STDOUT_ARTIFACT}."
        )
    return variables


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_versions() -> dict[str, str]:
    """Versions of the pinned stack, as resolved in the venv this runs in."""
    from importlib.metadata import PackageNotFoundError, version

    versions = {}
    for name in TRACKED_PACKAGES:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = "not-installed"
    return versions


def repo_revision(skill_root: Path) -> str:
    """Full SHA of the clone, for provenance. Best-effort — never fatal."""
    try:
        out = subprocess.run(
            ["git", "-C", str(skill_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def collect_figures(plots_dir: Path, output_dir: Path) -> list[Path]:
    """
    Copy the four figures to the output-dir root and verify each is non-empty, so
    `get_hpc_job_outputs(files=["surface_temperature_comparison.png"])` works
    without the caller knowing about the plots subdir.
    """
    collected: list[Path] = []
    missing: list[str] = []
    for stem in FIGURE_STEMS:
        for suffix in FIGURE_SUFFIXES:
            src = plots_dir / f"{stem}{suffix}"
            if not src.is_file() or src.stat().st_size == 0:
                missing.append(str(src))
                continue
            dest = output_dir / src.name
            if src.resolve() != dest.resolve():
                shutil.copy2(src, dest)
            collected.append(dest)
    if missing:
        raise SystemExit(
            "[water4energy] ERROR: the diagnostic exited 0 but these figures are "
            "missing or empty:\n"
            + "\n".join(f"[water4energy]   {m}" for m in missing)
        )
    return collected


def build_results(
    *,
    stdout_text: str,
    inputs: dict[str, Path],
    figures: list[Path],
    skill_root: Path,
    resolution: float | None,
    wall_seconds: float,
    checksum_inputs: bool,
) -> dict:
    """
    Assemble results.json: the parsed metrics plus the provenance a reported number
    has to be traceable to.
    """
    variables = parse_metrics_stdout(stdout_text)
    # The upstream default when --resolution is not passed through.
    grid = {"resolution_deg": resolution if resolution is not None else 1.0}

    input_provenance = {}
    for name, path in inputs.items():
        entry: dict = {"path": str(path), "bytes": path.stat().st_size}
        if checksum_inputs:
            entry["sha256"] = sha256(path)
        input_provenance[name] = entry

    return {
        "schema": RESULTS_SCHEMA,
        "status": "ok",
        "metrics_source": "stdout",
        "region_name": REGION_NAME,
        "grid": grid,
        "variables": variables,
        "figures": sorted(f.name for f in figures),
        "provenance": {
            "repo": {
                "url": os.environ.get("W4E_REPO_URL"),
                "ref": os.environ.get("W4E_REPO_REF"),
                "revision": repo_revision(skill_root),
            },
            "inputs": input_provenance,
            "python": sys.version.split()[0],
            "packages": package_versions(),
            "wall_seconds": round(wall_seconds, 1),
            "hostname": socket.gethostname(),
            "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        },
    }


def summarize(results: dict) -> None:
    """One line per variable+scope, so the job log reads as a result, not a dump."""
    for key, entry in sorted(results["variables"].items()):
        for scope in ("global", "region"):
            metrics = entry.get(scope)
            if not metrics:
                continue
            label = results["region_name"] if scope == "region" else "global"
            units = entry.get("units", "")
            print(
                f"[water4energy] {key} [{label}]: "
                f"r={metrics['correlation']:.4f} "
                f"RMSE={metrics['rmse']:.3f} {units} "
                f"bias={metrics['bias']:+.3f} {units}".rstrip()
            )


def main(argv=None) -> int:
    args = parse_args(argv)
    skill_root = Path(args.skill_root).expanduser().resolve()
    data_dir = Path(args.data_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    plots_dir = output_dir / PLOTS_SUBDIR

    script = skill_root / UPSTREAM_SCRIPT
    if not script.is_file():
        raise SystemExit(
            f"[water4energy] ERROR: {UPSTREAM_SCRIPT} not found in {skill_root}\n"
            f"[water4energy] The clone step in job.frontier.slurm did not produce the "
            f"expected tree; check $W4E_REPO_URL / $W4E_REPO_REF."
        )

    inputs = {
        "era5": resolve_input(args.era5, data_dir, skill_root),
        "e3sm": resolve_input(args.e3sm, data_dir, skill_root),
        "tva_boundary": resolve_input(args.tva_boundary, data_dir, skill_root),
    }
    preflight(inputs, data_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    plots_dir.mkdir(parents=True, exist_ok=True)

    cartopy_data = skill_root / "cartopy_data"
    if not cartopy_data.is_dir():
        print(
            f"[water4energy] WARNING: no bundled coastline cache at {cartopy_data}; "
            "cartopy may attempt a download",
            file=sys.stderr,
        )
        cartopy_data = None

    # Belt and braces: job.frontier.slurm sets both, but a non-interactive backend
    # and a writable config dir are hard requirements for matplotlib here.
    env = dict(os.environ)
    env.setdefault("MPLBACKEND", "Agg")
    env.setdefault("MPLCONFIGDIR", str(output_dir / ".mplconfig"))
    Path(env["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)

    cmd = build_upstream_argv(
        args,
        python=sys.executable,
        script=script,
        inputs=inputs,
        plots_dir=plots_dir,
        cartopy_data=cartopy_data,
    )

    print(f"[water4energy] repo revision: {repo_revision(skill_root)}")
    print(f"[water4energy] python: {sys.executable} ({sys.version.split()[0]})")
    print(f"[water4energy] running: {' '.join(cmd)}", flush=True)

    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=str(skill_root), env=env, text=True, capture_output=True)
    wall_seconds = time.monotonic() - started

    # Echo both streams into the job log, and keep stdout as an artifact next to
    # the figures — it carries the metric summary and any upstream warnings.
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    (output_dir / STDOUT_ARTIFACT).write_text(proc.stdout or "")

    if proc.returncode != 0:
        print(
            f"[water4energy] ERROR: {UPSTREAM_SCRIPT} exited {proc.returncode}",
            file=sys.stderr,
        )
        return proc.returncode

    figures = collect_figures(plots_dir, output_dir)
    for path in figures:
        # The UI auto-displays images announced this way; keep the wording stable.
        if path.suffix == ".png":
            print(f"Plot saved to {path}")

    results = build_results(
        stdout_text=proc.stdout or "",
        inputs=inputs,
        figures=figures,
        skill_root=skill_root,
        resolution=args.resolution,
        wall_seconds=wall_seconds,
        checksum_inputs=args.checksum_inputs,
    )
    results_path = output_dir / RESULTS_ARTIFACT
    results_path.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")

    summarize(results)
    print(f"[water4energy] metrics ({results['metrics_source']}) -> {results_path}")
    print(f"[water4energy] done in {wall_seconds:.1f}s. Outputs in {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
