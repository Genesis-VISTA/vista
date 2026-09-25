#!/usr/bin/env python3
"""
Vista HPC wrapper for the ``deepthermo-wl`` job (both stages of the engine).

Turns ONE flat order into a ``config.toml`` and an MPI run of DeepThermo's ``hea-wl``,
writing a small ``results.json`` into the output dir.

Two modes, same binary, different config (see the repo's docs/vae-workflow.md):

  --mode collect   PT warm-up with snapshot capture on and the WL outer loop
                   short-circuited (mod_factor_init < mod_factor_final). Produces the
                   training configurations the VAE is fitted to.
  --mode sample    The real Wang-Landau run, with the trained VAE proposing global
                   moves. Produces the density of states and vae.dat acceptance stats.

Artifacts live in a PERSISTENT workspace on shared storage, not in $VISTA_OUT: the
stages run as separate jobs, so collect's snapshots must still be there when the
training job starts, and the trained model must still be there when sampling starts.
$VISTA_OUT gets only results.json plus a couple of small diagnostics — a recursive
Globus listing of a directory full of snapshots would make a status check crawl.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path


def _run(cmd, **kw):
    print(f"[deepthermo-wl] $ {' '.join(str(c) for c in cmd)}", flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


# Files the engine updates DURING a run, small enough to mirror into $VISTA_OUT so the
# agent can show progress on a job that takes hours. DOS_H_iter<NNN>.dat is written by
# rank 0 every 100 WL sweeps and at each iteration boundary; with mod_factor going
# 1.0 -> 1e-6 by halving there are only ~20 of them, a few hundred lines each.
PROGRESS_GLOBS = ("DOS_H_iter*.dat", "vae.dat", "misc0.dat", "engine.log")
PROGRESS_MAX_FILES = 64
PROGRESS_MAX_BYTES = 4_000_000


def sync_progress(run_dir: Path, out_dir: Path) -> list[str]:
    """
    Mirror the engine's live diagnostics into the job output dir.

    Bounded on purpose: `get_hpc_job_status` recursively lists the output dir over
    Globus, so an unbounded mirror would make a status check crawl — the very thing
    keeping snapshots out of $VISTA_OUT avoids. Copies are best-effort; a file being
    rewritten mid-copy is picked up on the next pass.
    """
    copied: list[str] = []
    for pattern in PROGRESS_GLOBS:
        for src in sorted(run_dir.glob(pattern)):
            if len(copied) >= PROGRESS_MAX_FILES:
                return copied
            try:
                if src.stat().st_size > PROGRESS_MAX_BYTES:
                    continue
                dst = out_dir / src.name
                # Skip unchanged files so we are not re-writing the same bytes each pass.
                if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
                    continue
                shutil.copy2(src, dst)
                copied.append(src.name)
            except OSError:
                continue  # mid-write or vanished; next pass will get it
    return copied


# Everything the engine writes into its run dir. The workspace persists across jobs and
# each stage reuses the same run dir, so without clearing these a new run inherits the
# previous one's output: the first progress mirror would copy stale DOS_H_iter*.dat and
# report them as this run's progress, and a leftover state<N>.input checkpoint could let
# the engine resume from a configuration that no longer matches config.toml.
STALE_RUN_GLOBS = (
    "*.dat",            # DOS_H_iter*, vae, misc*, stat*, comm, compos, g, infer, mask, run, therm
    "snap_*.xyz",       # collect-stage configurations
    "mc*.input",        # checkpoints
    "state*.input",
    "engine.log",
)


def reset_run_dir(run_dir: Path) -> list[str]:
    """
    Remove the previous run's artifacts from this stage's run dir.

    Files only — never directories and never symlinks, because `models` is a symlink
    into the workspace and deleting it would throw away the trained VAE. Inputs the
    wrapper regenerates every run (config.toml, coupling.input) are left alone; they are
    overwritten anyway.
    """
    removed: list[str] = []
    for pattern in STALE_RUN_GLOBS:
        for f in sorted(run_dir.glob(pattern)):
            if f.is_symlink() or not f.is_file():
                continue
            try:
                f.unlink()
                removed.append(f.name)
            except OSError:
                continue
    return removed


def workspace_inventory(workspace: Path, per_dir: int = 8) -> dict:
    """
    Summarize what the workspace holds, without transferring any of it.

    `get_hpc_job_outputs` rejects ".." and absolute paths, so it can only reach
    $VISTA_OUT/<job_id>/ — the workspace is a sibling and is structurally unreachable
    from outside the job. Without this, "does the sampler have a trained model?" or
    "how many snapshots did collect produce?" can only be answered by running another
    job. Names and sizes are cheap; contents are not copied.
    """
    inv: dict = {"root": str(workspace)}
    if not workspace.is_dir():
        return {**inv, "exists": False}
    inv["exists"] = True
    for sub in ("collect", "train", "models", "sample"):
        d = workspace / sub
        if not d.is_dir():
            continue
        files = [f for f in sorted(d.iterdir()) if f.is_file()]
        try:
            total = sum(f.stat().st_size for f in files)
        except OSError:
            total = None
        inv[sub] = {
            "n_files": len(files),
            "bytes": total,
            "names": [f.name for f in files[:per_dir]],
            "truncated": len(files) > per_dir,
        }
    models = workspace / "models"
    if models.is_dir():
        exported = sorted(f.name for f in models.glob("encoder_*.pt"))
        # A bootstrap.pt beside the encoder means the pair may be random-weight; the
        # training stage does not write bootstrap.pt.
        inv["models_present"] = bool(exported)
        inv["encoders"] = exported
        inv["bootstrap_marker_present"] = (models / "bootstrap.pt").is_file()
    return inv


def _progress_thread(run_dir: Path, out_dir: Path, stop: "threading.Event", every: float):
    """Background mirror loop; runs while the engine does."""
    while not stop.wait(every):
        names = sync_progress(run_dir, out_dir)
        if names:
            print(f"[deepthermo-wl] progress: updated {len(names)} file(s) in "
                  f"$VISTA_OUT ({', '.join(names[:6])}"
                  f"{'...' if len(names) > 6 else ''})", flush=True)


def _run_tee(cmd, log_path, **kw):
    """Run a command, streaming its output live AND capturing it for parsing."""
    print(f"[deepthermo-wl] $ {' '.join(str(c) for c in cmd)}", flush=True)
    captured = []
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1, **kw, encoding="utf-8")
        for line in proc.stdout:
            print(line, end="", flush=True)
            log.write(line)
            captured.append(line)
    if proc.wait() != 0:
        raise SystemExit(f"engine exited {proc.returncode}")
    return "".join(captured)


_PT_ENERGY_RE = re.compile(r"ptEmin\s*=\s*(\S+)\s+ptEmax\s*=\s*(\S+)")


def observed_energy_window(engine_output: str, n_lattice: int) -> dict | None:
    """
    Turn the engine's `ptEmin = X  ptEmax = Y` line into a WL window suggestion.

    The engine prints TOTAL energies but the [wang_landau] window is per-site
    (main.cc divides by N^3 before comparing), so the conversion matters. A window
    whose lower edge sits below the reachable ground state never converges and
    nothing warns you — the run just spins — so the next stage should take its
    window from here rather than from a borrowed constant.
    """
    m = _PT_ENERGY_RE.search(engine_output or "")
    if not m:
        return None
    sites = n_lattice ** 3
    try:
        total_min, total_max = float(m.group(1)), float(m.group(2))
    except ValueError:
        return None
    per_min, per_max = total_min / sites, total_max / sites
    span = per_max - per_min
    # Inset slightly: the extremes were each visited once, so a window pinned exactly
    # to them is the hardest possible to flatten.
    lo, hi = per_min + 0.05 * span, per_max - 0.05 * span

    # bin_width is in TOTAL energy units, not per-site: wanglandau.cc computes
    #   bins = (WLD1max - WLD1min) / (bin_width * invN),   invN = 1/N^3
    # so the per-site bin is bin_width/N^3 and bin_width = per_site_span * N^3 / n_bins.
    # Getting this wrong is silent — too small just means a huge, never-flattening
    # histogram. Upstream's 0.0055 over their window is ~690 bins, so aim for the same
    # order rather than inventing one.
    n_bins = 600
    bin_width = ((hi - lo) * sites / n_bins) if hi > lo else None
    return {
        "units_note": (
            "The engine prints TOTAL energies; [wang_landau] e_min/e_max are PER SITE "
            "(total / N^3). Use suggested_window — never pt_e_total_DO_NOT_USE_AS_WINDOW."
        ),
        "pt_e_total_DO_NOT_USE_AS_WINDOW": [total_min, total_max],
        "pt_e_per_site": [per_min, per_max],
        "n_sites": sites,
        "suggested_window": {
            "e_min": round(lo, 6), "e_max": round(hi, 6),
            "bin_width": round(bin_width, 6) if bin_width else None,
            "approx_bins": n_bins,
        },
    }


def validate_window(e_min: float, e_max: float, recorded: dict | None) -> None:
    """
    Reject a WL window that cannot work, at submit time rather than after an hour.

    The failure this exists for: the engine reports TOTAL energies but [wang_landau]
    e_min/e_max are PER SITE. Passing the totals gives a window ~N^3 too wide and far
    below the reachable ground state, so the flatness loop never exits and the run
    spins silently until walltime.
    """
    if e_min >= e_max:
        raise SystemExit(f"--e-min ({e_min}) must be below --e-max ({e_max}).")
    if not recorded:
        return
    per = recorded.get("pt_e_per_site") or []
    sites = recorded.get("n_sites") or 0
    if len(per) != 2 or not sites:
        return
    lo, hi = per

    # Off by roughly the site count in the telltale direction => total energies.
    if abs(e_min) > abs(lo) * (sites ** 0.5):
        sug = recorded.get("suggested_window") or {}
        raise SystemExit(
            f"--e-min {e_min} / --e-max {e_max} look like TOTAL energies, but "
            f"[wang_landau] e_min/e_max are PER SITE (total / N^3 = total / {sites}).\n"
            f"This system's observed per-site range is [{lo:.6f}, {hi:.6f}].\n"
            f"Use e_min={sug.get('e_min')} e_max={sug.get('e_max')} "
            f"bin_width={sug.get('bin_width')}, or omit the flags and let the collect "
            "stage's recorded window be used."
        )

    # A window outside the sampled range can never be flattened.
    if e_max <= lo or e_min >= hi:
        raise SystemExit(
            f"WL window [{e_min}, {e_max}] does not overlap the energies parallel "
            f"tempering actually reached, [{lo:.6f}, {hi:.6f}]. Every masked bin must be "
            "visited for the flatness loop to exit, so this run would never converge."
        )


def _csv(text, cast=str):
    return [cast(p) for p in re.split(r"[,\s]+", text.strip()) if p]


def engine_grid(n_lattice: int) -> tuple[int, int]:
    """(grid, offset) for an N^3 lattice — mirrors vae-modeling/utils/geometry.py.

    The divide truncates, matching the C++ int cast, so the grid is NOT always a
    multiple of 16 (N=5 gives 15). Never 'round up to 16' here.
    """
    import math

    shift = n_lattice - 1
    raw = 2 * (n_lattice - 1) + shift + 1  # == 3N - 2
    pad = int((math.ceil(raw / 16) * 16 - raw) / 2)
    return raw + 2 * pad, shift + pad


def build_config(args, elements, composition) -> str:
    """Render config.toml. Collect mode short-circuits the WL loop; sample mode runs it."""
    collect = args.mode == "collect"
    # In collect mode mod_factor_init < mod_factor_final makes initWL()'s relax loop
    # exit immediately, so all the work is the PT warm-up that produces snapshots.
    wl_init = 0.001 if collect else args.mod_factor_init
    wl_final = 0.5 if collect else args.mod_factor_final
    lines = [
        "# Generated by vista's deepthermo-wl job. Do not edit by hand.",
        "[lattice]",
        f"N              = {args.n}",
        f"NE             = {len(elements)}",
        f"SH             = {args.sh}",
        "elements       = [" + ", ".join(f'"{e}"' for e in elements) + "]",
        "composition    = [" + ", ".join(str(c) for c in composition) + "]",
        f"nb_interaction = {args.nb_interaction}",
        f'coupling_file  = "{args.coupling_file}"',
        f"reglin_intercept = {args.intercept}",
        f"Z_R              = {args.z_r}",
        "",
        "[wang_landau]",
        f"bin_width            = {args.bin_width}",
        f"e_min                = {args.e_min}",
        f"e_max                = {args.e_max}",
        f"flatness             = {args.flatness}",
        f"mod_factor_init      = {wl_init}",
        f"iteration_factor     = {args.iteration_factor}",
        f"mod_factor_final     = {wl_final}",
        f"production_bin_samps = {1 if collect else args.production_bin_samps}",
        "",
        "[parallel_tempering]",
        "metropolis_sampling = false",
        f"T_init  = {args.t_init}",
        f"T_final = {args.t_final}",
        f"dT      = {args.dt}",
        f"samples = {args.samples}",
        f"sep     = {args.sep}",
        f"drop    = {args.drop}",
        "",
        "[thermodynamics]",
        f"T_init  = {args.thermo_t_init}",
        f"T_final = {args.thermo_t_final}",
        f"dT      = {args.thermo_dt}",
        "",
        "[model]",
        'dir = "models"',
    ]
    if collect:
        lines += [
            "",
            "[output]",
            f"snapshot_stride = {args.snapshot_stride}",
            f"snapshot_lowe   = {'true' if args.snapshot_lowe else 'false'}",
        ]
    return "\n".join(lines) + "\n"


def ensure_bootstrap_model(skill_root: Path, models: Path, alloy_tag: str,
                           grid: int, ne: int, latent_dim: int, python: str) -> bool:
    """
    Export a random-weight TorchScript pair if no model is present.

    The engine loads the encoder during PT (parallel_tempering computes its order
    parameter through encode()), so collect mode cannot start without a loadable
    model — even though the configurations it gathers come from Metropolis/PT physics
    and are unaffected by the weights. Only the order-parameter column is meaningless.
    """
    if (models / f"encoder_{alloy_tag}.pt").is_file():
        return False
    models.mkdir(parents=True, exist_ok=True)
    script = (
        "import torch, sys\n"
        "from pathlib import Path\n"
        "from src.export_torchscript import export\n"
        "from src.model import VAE, VAEConfig\n"
        f"out = Path(r'{models}')\n"
        f"cfg = VAEConfig(grid_size={grid}, num_elements={ne}, latent_dim={latent_dim})\n"
        "w = out / 'bootstrap.pt'\n"
        "torch.save(VAE(cfg).state_dict(), w)\n"
        f"export(w, '{alloy_tag}', grid_size={grid}, num_elements={ne}, "
        f"latent_dim={latent_dim}, out_dir=out)\n"
    )
    env = dict(os.environ, PYTHONPATH=str(skill_root / "vae-modeling" / "vae"))
    print("[deepthermo-wl] no model present; exporting random-weight bootstrap", flush=True)
    subprocess.run([python, "-c", script], check=True, env=env)
    return True


def parse_vae_dat(path: Path) -> dict:
    """Last row of vae.dat: sweeps, lnwlf, cumulative attempts, accepts, ratio."""
    if not path.is_file():
        return {}
    rows = [r.split() for r in path.read_text(encoding="utf-8").splitlines() if r.strip() and not r.startswith("#")]
    if not rows:
        return {}
    last = rows[-1]
    # Columns: sweeps, lnwlf, cumulative attempts, cumulative accepts, ratio.
    # (Upstream's own `awk '{print $1, $2, $5}'` confirms the ratio is the 5th field.)
    try:
        attempts, accepts = float(last[2]), float(last[3])
        return {
            "sweeps": float(last[0]),
            "lnwlf": float(last[1]),
            "vae_attempts": attempts,
            "vae_accepts": accepts,
            "vae_acceptance": (accepts / attempts) if attempts else None,
            "reported_ratio": float(last[4]) if len(last) > 4 else None,
        }
    except (IndexError, ValueError):
        return {"raw_last_row": " ".join(last)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run one DeepThermo-WL stage.")
    p.add_argument("--skill-root", required=True, help="Path to the cloned DeepThermo-WL repo.")
    p.add_argument("--engine-bin", required=True, help="Path to the built hea-wl binary.")
    p.add_argument("--output-dir", required=True, help="Where results.json lands ($VISTA_OUT).")
    p.add_argument("--workspace-root", required=True,
                   help="Root under which named workspaces live (set by the cluster script).")
    p.add_argument("--workspace", default="default",
                   help="Workspace NAME, shared by the three stages. Part of script_args so "
                        "the agent can pick one per study (submit_hpc_job passes no env).")
    p.add_argument("--python", default=sys.executable, help="Interpreter with torch available.")
    p.add_argument("--ranks", type=int, default=None, help="MPI ranks (= PT replicas).")
    p.add_argument("--launcher", default="srun")
    p.add_argument("--progress-interval", type=float, default=60.0, dest="progress_interval",
                   help="Seconds between mirroring DOS_H_iter*/vae.dat into the output dir "
                        "so a long run can be inspected while it runs. 0 disables.")

    p.add_argument("--mode", required=True, choices=("collect", "sample"))
    p.add_argument("--keep-existing", action="store_true", dest="keep_existing",
                   help="Do NOT clear the previous run's artifacts from this stage's run "
                        "dir. Off by default: a reused run dir otherwise makes stale DOS "
                        "files look like this run's progress.")
    p.add_argument("--seed", type=int, default=42)

    # Lattice / chemistry.
    p.add_argument("--n", type=int, default=10, help="Linear lattice size; sites = N^3.")
    p.add_argument("--elements", default="Mo,Nb,Ta,W")
    p.add_argument("--composition", default="0.25,0.25,0.25,0.25")
    p.add_argument("--sh", type=int, default=6)
    p.add_argument("--nb-interaction", type=int, default=1, dest="nb_interaction")
    p.add_argument("--coupling-file", default="coupling.input", dest="coupling_file")
    p.add_argument("--coupling-source", default=None, dest="coupling_source",
                   help="Copied into the run dir as --coupling-file. Default: the repo's example.")
    p.add_argument("--intercept", type=float, default=-1.2702430255548436)
    p.add_argument("--z-r", type=float, default=0.1, dest="z_r",
                   help="Latent step radius: trades VAE move size against acceptance.")
    p.add_argument("--latent-dim", type=int, default=3, dest="latent_dim")
    p.add_argument("--alloy-tag", default="MoNbTaW", dest="alloy_tag")

    # Parallel tempering (drives collection).
    p.add_argument("--t-init", type=float, default=10.0, dest="t_init")
    p.add_argument("--t-final", type=float, default=2000.0, dest="t_final")
    p.add_argument("--dt", type=float, default=0.1)
    p.add_argument("--samples", type=int, default=400, help="Frames per rank (collect).")
    p.add_argument("--sep", type=int, default=10, help="Sweeps between samples (decorrelation).")
    p.add_argument("--drop", type=int, default=100, help="Equilibration sweeps.")
    p.add_argument("--snapshot-stride", type=int, default=1, dest="snapshot_stride")
    p.add_argument("--snapshot-lowe", action="store_true", dest="snapshot_lowe")

    # Wang-Landau (drives sampling).
    # No defaults for the energy window. The repo's example values (-1.2808/-1.2770) are
    # MoNbTaW at N=10 with those particular DFT couplings and are meaningless elsewhere —
    # and a window whose lower edge sits below the reachable ground state NEVER converges,
    # with no warning: the run just spins until walltime. Unset, these are read from the
    # collect stage's observed energies via <workspace>/energy_window.json.
    p.add_argument("--bin-width", type=float, default=None, dest="bin_width")
    p.add_argument("--e-min", type=float, default=None, dest="e_min")
    p.add_argument("--e-max", type=float, default=None, dest="e_max")
    p.add_argument("--flatness", type=float, default=0.6)
    p.add_argument("--mod-factor-init", type=float, default=1.0, dest="mod_factor_init")
    p.add_argument("--iteration-factor", type=float, default=2.0, dest="iteration_factor")
    p.add_argument("--mod-factor-final", type=float, default=1.0e-6, dest="mod_factor_final")
    p.add_argument("--production-bin-samps", type=int, default=10, dest="production_bin_samps")

    # Thermodynamics sweep.
    p.add_argument("--thermo-t-init", type=float, default=100, dest="thermo_t_init")
    p.add_argument("--thermo-t-final", type=float, default=3000, dest="thermo_t_final")
    p.add_argument("--thermo-dt", type=float, default=2, dest="thermo_dt")

    args = p.parse_args(argv)

    skill_root = Path(args.skill_root).resolve()
    engine_bin = Path(args.engine_bin).resolve()
    out_dir = Path(args.output_dir).resolve()
    workspace = (Path(args.workspace_root) / args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not engine_bin.is_file():
        raise SystemExit(f"engine binary not found at {engine_bin}; did the build step run?")

    ranks = args.ranks or int(os.environ.get("SLURM_NTASKS") or 0)
    if ranks < 2:
        raise SystemExit(
            f"need at least 2 MPI ranks (each rank is one PT replica); got {ranks}."
        )

    elements = _csv(args.elements)
    composition = _csv(args.composition, float)
    if len(elements) != len(composition):
        raise SystemExit(
            f"--elements has {len(elements)} entries but --composition has "
            f"{len(composition)}; they index the same table."
        )
    total = sum(composition)
    if abs(total - 1.0) > 1e-3:
        raise SystemExit(f"composition must sum to 1.0 +/- 1e-3 (got {total:.6f})")

    # Resolve the WL window before anything expensive happens.
    if args.mode == "sample":
        suggested = {}
        wf = workspace / "energy_window.json"
        if wf.is_file():
            suggested = (json.loads(wf.read_text(encoding="utf-8")).get("suggested_window") or {})
        for field in ("e_min", "e_max", "bin_width"):
            if getattr(args, field) is None:
                if suggested.get(field) is None:
                    raise SystemExit(
                        f"--{field.replace('_', '-')} is not set and no usable "
                        f"{wf} was found.\n"
                        "The WL window must come from THIS system's energies: a window "
                        "below the reachable ground state never converges and the run "
                        "spins silently until walltime.\n"
                        "Run --mode collect against this workspace first (it records the "
                        "window), or pass the flag explicitly."
                    )
                setattr(args, field, suggested[field])
        validate_window(args.e_min, args.e_max,
                        json.loads(wf.read_text(encoding="utf-8")) if wf.is_file() else None)
        print(f"[deepthermo-wl] WL window: e_min={args.e_min} e_max={args.e_max} "
              f"bin_width={args.bin_width}", flush=True)

    grid, offset = engine_grid(args.n)
    run_dir = workspace / args.mode
    run_dir.mkdir(parents=True, exist_ok=True)

    # Clear the previous run's output before anything can mirror or read it.
    if args.keep_existing:
        cleared: list[str] = []
        print("[deepthermo-wl] --keep-existing: leaving previous artifacts in place; "
              "progress files may be stale until this run overwrites them", flush=True)
    else:
        cleared = reset_run_dir(run_dir)
        if cleared:
            print(f"[deepthermo-wl] cleared {len(cleared)} stale artifact(s) from "
                  f"{run_dir}: {', '.join(cleared[:8])}"
                  f"{'...' if len(cleared) > 8 else ''}", flush=True)
    models = workspace / "models"

    # The engine resolves [model] dir = "models" relative to its cwd.
    engine_models = run_dir / "models"
    if not engine_models.exists():
        engine_models.symlink_to(models, target_is_directory=True) if models.exists() \
            else engine_models.mkdir(parents=True, exist_ok=True)

    # Couplings: same format the alloy-thermo-skill emits; default to the repo example.
    src_coupling = Path(args.coupling_source) if args.coupling_source else (
        skill_root / "examples" / "frontier" / "coupling.input"
    )
    if not src_coupling.is_file():
        raise SystemExit(f"coupling file not found at {src_coupling}")
    (run_dir / args.coupling_file).write_bytes(src_coupling.read_bytes())

    bootstrapped = ensure_bootstrap_model(
        skill_root, engine_models if not engine_models.is_symlink() else models,
        args.alloy_tag, grid, len(elements), args.latent_dim, args.python,
    )

    config_text = build_config(args, elements, composition)
    (run_dir / "config.toml").write_text(config_text, encoding="utf-8")
    print(f"[deepthermo-wl] mode={args.mode} N={args.n} grid={grid} offset={offset} "
          f"ranks={ranks} workspace={workspace}", flush=True)

    stop_progress = threading.Event()
    mirror = threading.Thread(
        target=_progress_thread,
        args=(run_dir, out_dir, stop_progress, args.progress_interval),
        daemon=True,
    )
    sync_progress(run_dir, out_dir)      # don't make the first look wait an interval
    if args.progress_interval > 0:
        mirror.start()
    try:
        engine_out = _run_tee(
            [args.launcher, "-n", ranks, engine_bin, "config.toml", args.seed],
            run_dir / "engine.log", cwd=run_dir,
        )
    finally:
        stop_progress.set()
        if mirror.is_alive():
            mirror.join(timeout=10)
        sync_progress(run_dir, out_dir)   # final pass, so the last iteration lands
    energy = observed_energy_window(engine_out, args.n)

    snapshots = sorted(str(p.name) for p in run_dir.glob("snap_*.xyz"))
    results = {
        "job": "deepthermo-wl",
        "mode": args.mode,
        "workspace": str(workspace),
        "run_dir": str(run_dir),
        "lattice": {"N": args.n, "grid_size": grid, "coordinate_offset": offset,
                    "elements": elements, "composition": composition},
        "replicas": ranks,
        "seed": args.seed,
        "used_bootstrap_model": bootstrapped,
        "snapshots": snapshots[:50],
        "n_snapshot_files": len(snapshots),
        "observed_energy": energy,
        "workspace_inventory": workspace_inventory(workspace),
        "cleared_stale_artifacts": cleared,
    }
    if args.mode == "collect" and energy:
        # Hand the window to the sample stage through the workspace, so it is chosen
        # from this system's actual energies rather than a constant borrowed from the
        # repo's example (which is MoNbTaW at N=10 and valid for nothing else).
        (workspace / "energy_window.json").write_text(json.dumps(energy, indent=2), encoding="utf-8")
        print(f"[deepthermo-wl] suggested WL window: {energy['suggested_window']}", flush=True)
    if args.mode == "sample":
        results["vae_move"] = parse_vae_dat(run_dir / "vae.dat")
        dos = sorted(f.name for f in run_dir.glob("DOS_H_iter*.dat"))
        results["dos_files"] = dos
        results["wl_iterations_completed"] = len(dos)

    (out_dir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (out_dir / "config.toml").write_text(config_text, encoding="utf-8")  # diagnostics come from sync_progress

    print(f"[deepthermo-wl] wrote {out_dir / 'results.json'} "
          f"({len(snapshots)} snapshot file(s) left in {run_dir})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
