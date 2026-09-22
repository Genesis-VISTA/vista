#!/usr/bin/env python3
"""
Vista HPC wrapper for the ``refine-downscaling`` job.

Runs the REFINE demo package's own pipelines — unmodified, in place, from the
read-only Lustre staging directory — and writes a machine-readable
``results.json`` plus at least one displayable figure into the job's output dir.

This file is the only non-metadata file in ``hpc_jobs/refine-downscaling/``, so
vista Globus-stages it to ``$RUN_DIR_Frontier``. ``job.frontier.slurm`` invokes it
as::

    python run_downscaling.py --base-dir $REFINE_BASE_DIR --data-dir ... \
        --input-dir ... --checkpoint ... --env-prefix ... \
        --output-dir $VISTA_OUT <passthrough...>

The passthrough flags are the skill's ``script_args`` contract (see the job README).

Two properties are worth knowing before reading the code:

1. **Nothing here computes a metric.** Every number in ``results.json`` is read
   from JSON the upstream pipelines already write — ``<output>.nc.json`` from
   ``pipeline_04_infer.py``, ``evaluation_summary.json`` from
   ``pipeline_03_evaluate.py``, and ``spatial_statistics_index.json`` from
   ``utility_plot_spatial_statistics.py``. Nothing is scraped from stdout. A
   missing or unparseable upstream JSON is a hard failure, never a ``results.json``
   full of nulls.

2. **The base dir is read-only shared space.** Everything written goes under
   ``--output-dir``; pipeline paths are all passed absolute and ``cwd`` is the
   output dir, so a stray relative write cannot land in the shared package.

The only original rendering here is presentation: a coarse-vs-downscaled quicklook
for ``infer`` (upstream plots only evaluation statistics, which need truth) and a
model-vs-baseline bar chart for ``evaluate``. Neither computes anything the
pipelines did not already report.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

RESULTS_SCHEMA = "vista/refine-downscaling/results/v1"
RESULTS_ARTIFACT = "results.json"
STDOUT_ARTIFACT = "refine_stdout.txt"

# The single 6x route: these three variables, together. `stage2` is the legacy
# registry identifier for this same model.
VARIABLES = ("tmin", "tmax", "prcp")

# Upstream entry points, all at the demo package root.
INFER_SCRIPT = "pipeline_04_infer.py"
EVALUATE_SCRIPT = "pipeline_03_evaluate.py"
SPATIAL_PLOT_SCRIPT = "utility_plot_spatial_statistics.py"
MODEL_REGISTRY = "skill-authoring-kit/model-registry.json"

# Coarse inputs are one file per variable per year.
INPUT_TEMPLATE = "Daymet_ERA5_{variable}_dy_{year}_0p25deg.nc"

# The day guardrail. `--end-index` is zero-based and exclusive, so a full year is
# one typo away from a single day — and the agent is the one typing.
MAX_DAYS_WITHOUT_OVERRIDE = 31
# ~51 MB of uncompressed NetCDF per day (job README); used only to make a refusal
# concrete, never to decide anything.
INFER_BYTES_PER_DAY = 51 * 1024 * 1024
# A retained prediction day is 3 variables x 1368 x 3096 float32.
PREDICTION_BYTES_PER_DAY = 3 * 1368 * 3096 * 4

# Subdir for the evaluation pipeline's own output (summary, predictions, plots).
EVALUATION_SUBDIR = "evaluation"


class JobError(RuntimeError):
    """A failure that should be reported verbatim to the user and stop the job."""


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)

    # Wiring supplied by job.frontier.slurm from cluster_defaults.json.
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--env-prefix", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)

    # The skill's script_args contract starts here.
    parser.add_argument("--mode", choices=["infer", "evaluate"], default="infer")

    # infer
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--start-date", default="1990-01-01")

    # evaluate
    parser.add_argument("--split", choices=["train", "val", "test"], default="test")
    parser.add_argument("--max-days", type=int, default=1)
    parser.add_argument("--plots", action="store_true")
    parser.add_argument("--tile-rows", type=int, default=21)

    # both
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--allow-large", action="store_true")
    parser.add_argument("--checksum-inputs", action="store_true")
    return parser


def validate_args(args: argparse.Namespace, argv: list[str]) -> None:
    """
    Reject wrong flag/mode combinations *before* any work, and never silently
    ignore a flag the caller clearly meant to have an effect.
    """
    given = set(argv)

    def was_given(*flags: str) -> bool:
        return any(f in given or any(g.startswith(f + "=") for g in given) for f in flags)

    if args.mode != "evaluate":
        if args.plots:
            raise JobError(
                "--plots requires --mode evaluate: spatial statistics are computed "
                "against the held-out truth split, which inference does not have."
            )
        for flag in ("--split", "--max-days", "--tile-rows"):
            if was_given(flag):
                raise JobError(f"{flag} only applies to --mode evaluate; got --mode {args.mode}.")
    else:
        for flag in ("--days", "--start-date"):
            if was_given(flag):
                raise JobError(f"{flag} only applies to --mode infer; got --mode evaluate.")

    requested = args.days if args.mode == "infer" else args.max_days
    flag = "--days" if args.mode == "infer" else "--max-days"
    if requested < 1:
        raise JobError(f"{flag} must be at least 1; got {requested}.")

    if requested > MAX_DAYS_WITHOUT_OVERRIDE and not args.allow_large:
        per_day = INFER_BYTES_PER_DAY if args.mode == "infer" else PREDICTION_BYTES_PER_DAY
        projected_gb = (requested * per_day) / 1024**3
        what = "NetCDF output" if args.mode == "infer" else "retained predictions"
        raise JobError(
            f"{flag} {requested} exceeds the {MAX_DAYS_WITHOUT_OVERRIDE}-day guardrail "
            f"(projected {what}: ~{projected_gb:.1f} GB). Re-submit with --allow-large "
            f"if that volume is genuinely wanted."
        )


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(path: Path, what: str) -> Path:
    """Stat one required asset, failing with the exact path rather than a traceback."""
    if not path.exists():
        raise JobError(
            f"missing required {what}: {path}\n"
            "These assets are pre-staged read-only on Lustre and cannot be recreated "
            "by resubmitting — a git clone does not carry them. Check the REFINE_* "
            "entries in hpc_jobs/refine-downscaling/cluster_defaults.json."
        )
    return path


def load_manifest(data_dir: Path) -> dict:
    manifest_path = require(data_dir / "manifest.json", "prepared manifest")
    try:
        return json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        raise JobError(f"prepared manifest is not valid JSON: {manifest_path}: {exc}") from exc


def verify_checkpoint(base_dir: Path, checkpoint: Path, warnings: list[str]) -> str:
    """
    Hash the checkpoint and, when the demo's model registry is present, check it
    against the released 6x model. A mismatch is fatal: metrics from an unknown
    checkpoint are not comparable to the published reference values.
    """
    digest = sha256(checkpoint)
    registry_path = base_dir / MODEL_REGISTRY
    if not registry_path.exists():
        warnings.append(
            f"model registry not found at {registry_path}; checkpoint hash recorded "
            "but not verified against the released model."
        )
        return digest
    try:
        registry = json.loads(registry_path.read_text())
        expected = registry["models"]["stage2"]["checkpoint_sha256"]
    except (json.JSONDecodeError, KeyError) as exc:
        warnings.append(f"model registry at {registry_path} is unreadable ({exc}); hash not verified.")
        return digest
    if digest != expected:
        raise JobError(
            f"checkpoint SHA256 mismatch for {checkpoint}\n"
            f"  expected (registry): {expected}\n"
            f"  actual:              {digest}\n"
            "The staged weights are not the released 6x model, so results are not "
            "comparable to the published 1990 reference metrics. Stop and report this "
            "rather than treating the run as valid."
        )
    return digest


def preflight(args: argparse.Namespace, warnings: list[str]) -> dict:
    """Stat everything the run needs, then report what was verified."""
    base_dir = require(args.base_dir, "demo package root")
    require(base_dir / INFER_SCRIPT, "inference pipeline")
    require(args.data_dir, "prepared data dir")
    checkpoint = require(args.checkpoint, "checkpoint")
    if args.env_prefix is not None:
        require(args.env_prefix, "conda environment")

    manifest = load_manifest(args.data_dir)
    checkpoint_sha = verify_checkpoint(base_dir, checkpoint, warnings)

    inputs: dict[str, str] = {}
    if args.mode == "infer":
        require(args.input_dir, "coarse input dir")
        year = parse_start_date(args.start_date).year
        for variable in VARIABLES:
            path = require(
                args.input_dir / INPUT_TEMPLATE.format(variable=variable, year=year),
                f"{variable} input for {year}",
            )
            inputs[variable] = str(path)
    else:
        require(base_dir / EVALUATE_SCRIPT, "evaluation pipeline")
        if args.plots:
            require(base_dir / SPATIAL_PLOT_SCRIPT, "spatial statistics utility")
        splits = manifest.get("splits", {})
        if args.split not in splits:
            raise JobError(
                f"split {args.split!r} is not in the prepared manifest "
                f"(available: {', '.join(sorted(splits)) or 'none'})."
            )

    return {
        "manifest": manifest,
        "checkpoint_sha256": checkpoint_sha,
        "inputs": inputs,
        "input_checksums": (
            {name: sha256(Path(p)) for name, p in inputs.items()} if args.checksum_inputs else {}
        ),
    }


def parse_start_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise JobError(f"--start-date must be YYYY-MM-DD; got {value!r}") from exc


def resolve_interval(args: argparse.Namespace, manifest: dict) -> tuple[dt.date, int, int]:
    """
    Map a human start date onto the pipeline's index arithmetic.

    The bundled coarse inputs are one file per calendar year, so index zero is
    1 January of that year. `--start-index` is the offset into the file and
    `--end-index` is exclusive.
    """
    start = parse_start_date(args.start_date)
    year_start = dt.date(start.year, 1, 1)
    start_index = (start - year_start).days
    end_index = start_index + args.days

    year_lengths = manifest.get("year_lengths", {})
    available = year_lengths.get(str(start.year))
    if available is None:
        raise JobError(
            f"the prepared manifest has no year {start.year} "
            f"(available: {', '.join(sorted(year_lengths)) or 'none'})."
        )
    if end_index > available:
        raise JobError(
            f"--start-date {start.isoformat()} plus --days {args.days} runs past the end "
            f"of {start.year} ({available} days; requested index {end_index}). "
            "Downscale within a single calendar year."
        )
    return year_start, start_index, end_index


# ---------------------------------------------------------------------------
# Running the upstream pipelines
# ---------------------------------------------------------------------------
def run_pipeline(argv: list[str], cwd: Path, log_handle) -> None:
    """
    Run one upstream pipeline, streaming its output to both our stdout and the
    run log. A non-zero exit is fatal and is reported with the exact command.
    """
    printable = shlex.join(argv)
    banner = f"[refine] $ {printable}"
    print(banner, flush=True)
    log_handle.write(banner + "\n")
    log_handle.flush()

    process = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        log_handle.write(line)
    log_handle.flush()
    code = process.wait()
    if code != 0:
        raise JobError(f"pipeline exited {code}: {printable}\nSee {STDOUT_ARTIFACT} for its output.")


def read_upstream_json(path: Path, produced_by: str) -> dict:
    """
    Read a JSON file an upstream pipeline was supposed to write. Absence or a
    parse failure is fatal by design — a results.json full of nulls would look
    like a successful run that measured nothing.
    """
    if not path.exists():
        raise JobError(
            f"{produced_by} did not write its expected output at {path}. "
            "The pipeline's output contract changed or the run died after its last "
            f"log line; see {STDOUT_ARTIFACT}."
        )
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise JobError(f"output of {produced_by} at {path} is not valid JSON: {exc}") from exc


def do_infer(args: argparse.Namespace, checks: dict, log_handle) -> dict:
    output_path = args.output_dir / f"inference-{job_id()}.nc"
    year_start, start_index, end_index = resolve_interval(args, checks["manifest"])

    argv = [
        sys.executable, "-u", str(args.base_dir / INFER_SCRIPT),
        "--data-dir", str(args.data_dir),
        "--checkpoint", str(args.checkpoint),
        "--output", str(output_path),
        "--start-date", year_start.isoformat(),
        "--start-index", str(start_index),
        "--end-index", str(end_index),
        "--format", "netcdf",
        "--batch-size", str(args.batch_size),
        "--amp",
        "--enforce-temperature-order",
    ]
    for variable, path in checks["inputs"].items():
        argv += ["--input", f"{variable}={path}"]

    run_pipeline(argv, cwd=args.output_dir, log_handle=log_handle)

    # pipeline_04_infer.py writes its metadata beside the output as <output>.nc.json
    metadata_path = output_path.with_suffix(output_path.suffix + ".json")
    metadata = read_upstream_json(metadata_path, INFER_SCRIPT)
    return {"run": metadata, "output_path": output_path, "commands": [argv]}


def do_evaluate(args: argparse.Namespace, log_handle) -> dict:
    evaluation_dir = args.output_dir / EVALUATION_SUBDIR

    argv = [
        sys.executable, "-u", str(args.base_dir / EVALUATE_SCRIPT),
        "--data-dir", str(args.data_dir),
        "--checkpoint", str(args.checkpoint),
        "--output-dir", str(evaluation_dir),
        "--split", args.split,
        "--max-days", str(args.max_days),
        "--batch-size", str(args.batch_size),
        "--amp",
        "--enforce-temperature-order",
    ]
    # Predictions are ~18 GB for a full year and are only needed to draw the
    # spatial statistics, so they are discarded unless --plots asked for them.
    if not args.plots:
        argv.append("--no-save-predictions")

    run_pipeline(argv, cwd=args.output_dir, log_handle=log_handle)
    summary = read_upstream_json(evaluation_dir / "evaluation_summary.json", EVALUATE_SCRIPT)

    spatial = None
    plot_argv = None
    if args.plots:
        # The prepared index uses the `netcdf_patch_index` layout, for which the
        # evaluation pipeline's own in-line plotting never fires — the spatial
        # statistics have to come from the standalone utility.
        plot_argv = [
            sys.executable, "-u", str(args.base_dir / SPATIAL_PLOT_SCRIPT),
            "--data-dir", str(args.data_dir),
            "--evaluation-dir", str(evaluation_dir),
            "--split", args.split,
            "--tile-rows", str(args.tile_rows),
        ]
        run_pipeline(plot_argv, cwd=args.output_dir, log_handle=log_handle)
        spatial = read_upstream_json(
            evaluation_dir / "spatial_statistics_index.json", SPATIAL_PLOT_SCRIPT
        )

    return {
        "summary": summary,
        "spatial": spatial,
        "evaluation_dir": evaluation_dir,
        "commands": [argv] if plot_argv is None else [argv, plot_argv],
    }


# ---------------------------------------------------------------------------
# Figures (presentation only — nothing here computes a metric)
# ---------------------------------------------------------------------------
def draw_quicklook(
    args: argparse.Namespace, output_path: Path, metadata: dict, warnings: list[str]
) -> list[Path]:
    """
    One coarse-input vs downscaled-output panel per variable, first timestep.

    Upstream has no plot for raw inference output — its only figures are the
    evaluation spatial statistics, which need truth. Without this a bare infer run
    would hand the agent numbers and nothing to display.
    """
    figures: list[Path] = []
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        from netCDF4 import Dataset
    except ImportError as exc:  # pragma: no cover - depends on the Frontier env
        warnings.append(f"quicklook skipped: plotting dependencies unavailable ({exc}).")
        return figures

    units = {
        name: meta.get("units", "")
        for name, meta in (metadata.get("variable_metadata") or {}).items()
    }
    try:
        with Dataset(output_path) as downscaled:
            for variable in metadata.get("variables", VARIABLES):
                if variable not in downscaled.variables:
                    warnings.append(f"quicklook skipped for {variable}: not in {output_path.name}.")
                    continue
                fine = np.asarray(downscaled.variables[variable][0])
                coarse = read_coarse_first_step(args, variable, warnings)

                panels = 2 if coarse is not None else 1
                figure, axes = plt.subplots(
                    1, panels, figsize=(7 * panels, 4.2), layout="constrained"
                )
                axes = np.atleast_1d(axes)
                # One shared color scale, so the two panels are actually comparable;
                # percentile limits so a few extreme cells cannot flatten the field.
                finite = fine[np.isfinite(fine)]
                low, high = (
                    (float(np.percentile(finite, 1)), float(np.percentile(finite, 99)))
                    if finite.size else (None, None)
                )
                if coarse is not None:
                    axes[0].imshow(coarse, origin="lower", vmin=low, vmax=high, aspect="auto")
                    axes[0].set_title(f"coarse input  1/4 deg  {coarse.shape[0]}x{coarse.shape[1]}")
                image = axes[-1].imshow(fine, origin="lower", vmin=low, vmax=high, aspect="auto")
                axes[-1].set_title(f"REFINE 6x  1/24 deg  {fine.shape[0]}x{fine.shape[1]}")
                for axis in axes:
                    axis.set_xticks([])
                    axis.set_yticks([])
                # One colorbar: both panels share the scale, so two would imply
                # they do not.
                unit = units.get(variable, "")
                figure.colorbar(
                    image, ax=list(axes), orientation="horizontal",
                    fraction=0.06, pad=0.02, label=unit or None,
                )
                figure.suptitle(
                    f"{variable}" + (f" [{unit}]" if unit else "")
                    + f"  —  {metadata.get('start_date', '')}  (grid indexes, not coordinates)"
                )
                path = args.output_dir / f"quicklook_{variable}.png"
                figure.savefig(path, dpi=150)
                plt.close(figure)
                figures.append(path)
    except Exception as exc:  # noqa: BLE001 - see below
        # Deliberately broad: the GPU work has already succeeded by this point, and
        # no plotting problem justifies throwing that run away.
        warnings.append(f"quicklook failed after a successful run: {exc!r}")
    return figures


def read_coarse_first_step(args: argparse.Namespace, variable: str, warnings: list[str]):
    """The matching coarse field, for the side-by-side panel. Best effort."""
    try:
        import numpy as np
        from netCDF4 import Dataset

        year = parse_start_date(args.start_date).year
        path = args.input_dir / INPUT_TEMPLATE.format(variable=variable, year=year)
        index = (parse_start_date(args.start_date) - dt.date(year, 1, 1)).days
        with Dataset(path) as source:
            # prcp files may name the variable `pr`; mirror the pipeline's lookup.
            for candidate in (variable, f"{variable}_dy", "pr", "precipitation"):
                if candidate in source.variables:
                    return np.asarray(source.variables[candidate][index])
        warnings.append(f"quicklook: no recognizable variable for {variable} in {path.name}.")
    except Exception as exc:  # noqa: BLE001 - the panel is optional; the run is not
        warnings.append(f"quicklook: coarse panel unavailable for {variable}: {exc!r}")
    return None


def draw_metrics_chart(output_dir: Path, summary: dict, warnings: list[str]) -> list[Path]:
    """
    Model vs bilinear-baseline MAE and RMSE per variable, straight from
    evaluation_summary.json. Renders numbers the pipeline already reported so an
    evaluate run without --plots still has something to display.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        warnings.append(f"metrics chart skipped: matplotlib unavailable ({exc}).")
        return []

    try:
        metrics = summary.get("metrics") or {}
        variables = [v for v in summary.get("variables", VARIABLES) if v in metrics]
        if not variables:
            warnings.append("metrics chart skipped: evaluation summary carries no per-variable metrics.")
            return []

        figure, axes = plt.subplots(1, 2, figsize=(11, 4.2))
        positions = np.arange(len(variables))
        for axis, statistic in zip(axes, ("mae", "rmse")):
            model = [metrics[v]["model"][statistic] for v in variables]
            baseline = [metrics[v]["bilinear_baseline"][statistic] for v in variables]
            axis.bar(positions - 0.2, baseline, 0.4, label="bilinear baseline")
            axis.bar(positions + 0.2, model, 0.4, label="REFINE model")
            axis.set_xticks(positions)
            axis.set_xticklabels(variables)
            axis.set_title(statistic.upper())
            axis.legend()
        units = ", ".join(
            f"{v}: {(summary.get('variable_metadata') or {}).get(v, {}).get('units', '?')}"
            for v in variables
        )
        figure.suptitle(
            f"{summary.get('split', '?')} split, {summary.get('days', '?')} day(s) — {units}"
        )
        figure.tight_layout()
        path = output_dir / "metrics_comparison.png"
        figure.savefig(path, dpi=150)
        plt.close(figure)
        return [path]
    except Exception as exc:  # noqa: BLE001 - a chart failure must not discard measured metrics
        warnings.append(f"metrics chart failed: {exc!r}")
        return []


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
def job_id() -> str:
    return os.environ.get("SLURM_JOB_ID", "local")


def artifact_name(path: Path, output_dir: Path) -> str:
    """Name an artifact relative to the output dir so results.json stays portable."""
    try:
        return str(Path(path).resolve().relative_to(Path(output_dir).resolve()))
    except ValueError:
        return Path(path).name


def relative_artifact(path: Path, output_dir: Path, kind: str) -> dict | None:
    """Record a bulk artifact by path and size rather than moving it anywhere."""
    if not path.exists():
        return None
    return {
        "path": artifact_name(path, output_dir),
        "bytes": path.stat().st_size,
        "kind": kind,
    }


def torch_provenance(warnings: list[str]) -> dict:
    """
    Record what the GPU actually was. This is also how task 1.4 is answered — a
    run whose `cuda_available` is false silently fell back to CPU.
    """
    info: dict = {}
    try:
        import torch

        info["torch"] = torch.__version__
        info["cuda_available"] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            info["gpu"] = torch.cuda.get_device_name(0)
        else:
            warnings.append("torch reports no GPU: this run used the CPU and will be far slower.")
        version = getattr(torch, "version", None)
        if getattr(version, "hip", None):
            info["rocm"] = version.hip
    except Exception as exc:  # noqa: BLE001 - provenance is best effort, never fatal
        warnings.append(f"torch provenance unavailable: {exc!r}")
    return info


def main() -> int:
    argv = sys.argv[1:]
    args = build_parser().parse_args(argv)
    started = time.monotonic()
    warnings: list[str] = []

    args.output_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.output_dir / STDOUT_ARTIFACT

    try:
        validate_args(args, argv)
        checks = preflight(args, warnings)

        with log_path.open("w") as log_handle:
            if args.mode == "infer":
                outcome = do_infer(args, checks, log_handle)
            else:
                outcome = do_evaluate(args, log_handle)

        results: dict = {
            "schema": RESULTS_SCHEMA,
            "status": "ok",
            "mode": args.mode,
            "figures": [],
            "artifacts": [],
        }

        if args.mode == "infer":
            metadata = outcome["run"]
            results["run"] = {
                key: metadata.get(key)
                for key in (
                    "variables", "output_timesteps", "start_date",
                    "source_start_index", "source_end_index",
                    "temperature_order_enforced", "variable_metadata",
                )
            }
            figures = draw_quicklook(args, outcome["output_path"], metadata, warnings)
            for path, kind in (
                (outcome["output_path"], "netcdf"),
                (outcome["output_path"].with_suffix(outcome["output_path"].suffix + ".json"), "json"),
            ):
                entry = relative_artifact(path, args.output_dir, kind)
                if entry:
                    results["artifacts"].append(entry)
        else:
            summary = outcome["summary"]
            results["metrics"] = summary.get("metrics")
            results["run"] = {
                key: summary.get(key)
                for key in (
                    "split", "days", "variables", "parameters",
                    "temperature_order_enforced",
                    "temperature_order_violation_fraction",
                    "variable_metadata",
                )
            }
            figures = draw_metrics_chart(args.output_dir, summary, warnings)
            if outcome["spatial"] is not None:
                results["spatial_statistics"] = {
                    "statistics": outcome["spatial"].get("statistics"),
                    "days": outcome["spatial"].get("days"),
                }
                for plot in outcome["spatial"].get("plots", []):
                    figures.append(Path(plot))
            for name, kind in (
                ("predictions.npy", "predictions"),
                ("spatial_statistics_1990.npz", "arrays"),
                ("evaluation_summary.json", "json"),
            ):
                entry = relative_artifact(outcome["evaluation_dir"] / name, args.output_dir, kind)
                if entry:
                    results["artifacts"].append(entry)

        results["figures"] = [artifact_name(path, args.output_dir) for path in figures]

        results["provenance"] = {
            "checkpoint": str(args.checkpoint),
            "checkpoint_sha256": checks["checkpoint_sha256"],
            "base_dir": str(args.base_dir),
            "data_dir": str(args.data_dir),
            "env_prefix": str(args.env_prefix) if args.env_prefix else None,
            "inputs": checks["inputs"],
            "input_sha256": checks["input_checksums"],
            "commands": outcome["commands"],
            "wrapper_argv": argv,
            "wall_seconds": round(time.monotonic() - started, 2),
            "hostname": socket.gethostname(),
            "python": platform.python_version(),
            "slurm_job_id": job_id(),
            **torch_provenance(warnings),
        }
        if warnings:
            results["warnings"] = warnings

        (args.output_dir / RESULTS_ARTIFACT).write_text(json.dumps(results, indent=2) + "\n")
        print(f"[refine] wrote {RESULTS_ARTIFACT} ({args.mode}, {len(results['figures'])} figure(s))")
        for warning in warnings:
            print(f"[refine] WARNING: {warning}", file=sys.stderr)
        return 0

    except JobError as exc:
        print(f"[refine] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
