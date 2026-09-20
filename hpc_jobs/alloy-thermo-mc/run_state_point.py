#!/usr/bin/env python3
"""
Vista HPC wrapper for the ``alloy-thermo-mc`` job.

Turns ONE flat composition order into a parallel-tempering Monte Carlo run using the
cloned ``alloy-thermo-skill`` repo, writing ``results.json`` into the output dir.

This file is the only non-metadata file in ``hpc_jobs/alloy-thermo-mc/``, so vista
Globus-stages it to ``$RUN_DIR_<Cluster>``. The cluster script invokes it as::

    python run_state_point.py --skill-root <clone> --engine-bin <clone>/engine/alloy_mc \
        --output-dir $VISTA_OUT --ranks $NRANKS <order...>

It runs SERIALLY (not under srun) and launches the MPI engine itself, because the
engine's rank count IS the temperature-ladder size and must be chosen here.

Pipeline
--------
    composition  -> spec.json      (DFT couplings reused from the repo's MoNbTaW example)
    spec.json    -> make_inputs.py -> composition/coupling/control.input
    control.input-> srun alloy_mc  -> thermo_run<i>.csv         (the parallel step)
    csv          -> analyze.py     -> summary.json
    summary.json -> results.json   (campaign-facing metrics)

Only ``results.json`` (plus the small analysis artifacts) is written to --output-dir;
the clone, the build, and the run directory stay in node-local scratch. Keeping the
output dir to a handful of files is what keeps ``get_hpc_job_status``' recursive
Globus listing fast (see the job README).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Composition must lie on the simplex; the engine only warns, the campaign must not.
SIMPLEX_TOL = 1e-3
ELEMENTS = ("Mo", "Nb", "Ta", "W")


def _run(cmd, **kw):
    """Run a command, echoing it, and fail loudly with its output on error."""
    print(f"[alloy-thermo-mc] $ {' '.join(str(c) for c in cmd)}", flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def build_spec(base_spec: dict, args) -> dict:
    """Overlay the candidate composition + run knobs onto the repo's DFT spec."""
    spec = json.loads(json.dumps(base_spec))  # deep copy
    comp = [args.mo, args.nb, args.ta, args.w]
    total = sum(comp)
    if abs(total - 1.0) > SIMPLEX_TOL:
        raise SystemExit(
            f"composition must sum to 1.0 +/- {SIMPLEX_TOL} "
            f"(got Mo={args.mo} Nb={args.nb} Ta={args.ta} W={args.w}, sum={total:.6f})"
        )
    if any(c < 0.0 for c in comp):
        raise SystemExit(f"composition fractions must be non-negative (got {comp})")
    if spec.get("elements") != list(ELEMENTS):
        raise SystemExit(
            f"base spec declares elements {spec.get('elements')}, expected {list(ELEMENTS)}; "
            "the --mo/--nb/--ta/--w order would be wrong for this spec."
        )

    spec["composition"] = comp
    if args.lattice:
        spec["lattice"] = args.lattice
    if args.n_shells is not None:
        spec["n_shells"] = args.n_shells

    sim = spec.setdefault("simulation", {})
    for key, val in (
        ("N", args.n),
        ("T_init", args.t_init),
        ("T_final", args.t_final),
        ("n_runs", args.n_runs),
        ("n_drop", args.n_drop),
        ("n_samples", args.n_samples),
        ("n_separation", args.n_separation),
        ("walltime_hours", args.walltime_hours),
    ):
        if val is not None:
            sim[key] = val
    return spec


def summarize(summary: dict, spec: dict, args, ranks: int) -> dict:
    """Turn analyze.py's summary.json into the campaign-facing results.json."""
    tc_cv = summary.get("Tc_from_specific_heat")
    tc_chi = summary.get("Tc_from_susceptibility")
    temps = summary.get("T") or []
    mean = summary.get("mean") or {}

    def series_at_min_T(name):
        """Value of a column at the lowest temperature in the ladder."""
        vals = mean.get(name)
        if not vals or not temps:
            return None
        return vals[temps.index(min(temps))]

    def series_avg(name):
        vals = mean.get(name)
        if not vals:
            return None
        return sum(vals) / len(vals)

    # Run-quality signals (advisory — reported, never gating, per the campaign scorer).
    peak_bracketed = None
    if tc_cv is not None and temps:
        lo, hi = min(temps), max(temps)
        span = hi - lo
        # A peak pinned to either endpoint means the ladder did not bracket it.
        peak_bracketed = bool(span > 0 and (tc_cv - lo) > 0.02 * span and (hi - tc_cv) > 0.02 * span)

    estimators_agree = None
    if tc_cv is not None and tc_chi is not None:
        # Same 15% rule analyze.py uses for its own "consistent" verdict.
        estimators_agree = bool(abs(tc_cv - tc_chi) < 0.15 * max(tc_cv, 1.0))

    swap_accept = series_avg("swap_accept")
    sro_alpha1 = series_at_min_T("alpha_mean")

    return {
        "job": "alloy-thermo-mc",
        "composition": dict(zip(ELEMENTS, spec["composition"])),
        "metrics": {
            "Tc_cv_K": tc_cv,
            "Tc_chi_K": tc_chi,
            "cv_peak": summary.get("specific_heat_peak"),
            "chi_peak": summary.get("susceptibility_peak"),
            "sro_alpha1": sro_alpha1,
            "swap_accept_mean": swap_accept,
            "peak_bracketed": peak_bracketed,
            "estimators_agree": estimators_agree,
        },
        "run": {
            "lattice": spec.get("lattice"),
            "n_shells": spec.get("n_shells"),
            "simulation": spec.get("simulation"),
            "replicas": ranks,
            "seed": args.seed,
            "n_temperatures": summary.get("n_temperatures"),
            "T_range": summary.get("T_range"),
        },
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run one MoNbTaW composition through PT Monte Carlo.")
    p.add_argument("--skill-root", required=True, help="Path to the cloned alloy-thermo-skill repo.")
    p.add_argument("--engine-bin", required=True, help="Path to the built alloy_mc binary.")
    p.add_argument("--output-dir", required=True, help="Where results.json lands (usually $VISTA_OUT).")
    p.add_argument("--work-dir", default=None, help="Scratch dir for the run (default: alongside the clone).")
    p.add_argument("--base-spec", default=None,
                   help="Spec carrying the DFT couplings (default: <skill-root>/examples/MoNbTaW/spec.json).")

    # Composition — the campaign's design variables. Must sum to 1.0.
    p.add_argument("--mo", type=float, required=True, help="Mo atom fraction.")
    p.add_argument("--nb", type=float, required=True, help="Nb atom fraction.")
    p.add_argument("--ta", type=float, required=True, help="Ta atom fraction.")
    p.add_argument("--w", type=float, required=True, help="W atom fraction.")

    # Model options.
    p.add_argument("--lattice", default=None, help="bcc | fcc | sc (default: from the base spec).")
    p.add_argument("--n-shells", type=int, default=None, dest="n_shells")

    # Sampling options. Cost scales as n_runs * (n_drop + n_samples) * N^3.
    #
    # Defaults are the CHEAP campaign-screening values (~5 min on 112 ranks), NOT the
    # base spec's production values (N=16, n_runs=4, 100k sweeps ~ 2 h). A bare
    # submission must not burn a 2-hour allocation by accident; ask for production
    # fidelity explicitly. See the job README on calibrating these.
    p.add_argument("--n", type=int, default=12, help="Linear lattice size; sites = N^3.")
    p.add_argument("--t-init", type=float, default=200.0, dest="t_init", help="Ladder low end (K).")
    p.add_argument("--t-final", type=float, default=2000.0, dest="t_final", help="Ladder high end (K).")
    p.add_argument("--n-runs", type=int, default=2, dest="n_runs",
                   help="Independent runs. NOTE: sequential per rank — multiplies walltime.")
    p.add_argument("--n-drop", type=float, default=5000.0, dest="n_drop", help="Equilibration sweeps.")
    p.add_argument("--n-samples", type=float, default=20000.0, dest="n_samples", help="Measurement samples.")
    p.add_argument("--n-separation", type=float, default=1.0, dest="n_separation")
    p.add_argument("--walltime-hours", type=float, default=0.07, dest="walltime_hours",
                   help="Engine's soft budget; it checkpoints near 0.9x and exits.")

    # Launch options.
    p.add_argument("--ranks", type=int, default=None,
                   help="MPI ranks == temperature replicas. Default: $SLURM_NTASKS.")
    p.add_argument("--launcher", default="srun", help="Parallel launcher (srun | mpirun).")
    p.add_argument("--seed", type=int, default=12345, help="Base RNG seed.")

    args = p.parse_args(argv)

    skill_root = Path(args.skill_root).resolve()
    engine_bin = Path(args.engine_bin).resolve()
    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not engine_bin.is_file():
        raise SystemExit(f"engine binary not found at {engine_bin}; did the build step run?")

    ranks = args.ranks or int(os.environ.get("SLURM_NTASKS") or 0)
    if ranks < 2:
        raise SystemExit(
            f"need at least 2 MPI ranks (each rank is one temperature replica); got {ranks}. "
            "Pass --ranks or run inside a Slurm allocation."
        )

    base_spec_path = Path(args.base_spec) if args.base_spec else skill_root / "examples" / "MoNbTaW" / "spec.json"
    if not base_spec_path.is_file():
        raise SystemExit(f"base spec not found at {base_spec_path}")
    base_spec = json.loads(base_spec_path.read_text())

    work = Path(args.work_dir).resolve() if args.work_dir else skill_root.parent / "alloy-run"
    run_dir = work / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    # 1. Candidate spec (DFT couplings from the repo + this composition + run knobs).
    spec = build_spec(base_spec, args)
    spec_path = work / "spec.json"
    spec_path.write_text(json.dumps(spec, indent=2))
    print(f"[alloy-thermo-mc] composition {dict(zip(ELEMENTS, spec['composition']))} "
          f"N={spec['simulation'].get('N')} ranks={ranks}", flush=True)

    # 2. Engine input files.
    _run([sys.executable, skill_root / "scripts" / "make_inputs.py", spec_path,
          "--outdir", run_dir, "--control-name", "control.input"], cwd=skill_root)

    # 3. The parallel step: one rank per temperature replica, run from run_dir so the
    #    engine finds composition.input / coupling.input by their fixed names.
    # Both srun and mpirun spell the task count "-n N".
    _run([args.launcher, "-n", ranks, engine_bin, "control.input", args.seed], cwd=run_dir)

    # 4. Analysis -> summary.json + figures.
    analysis_dir = run_dir / "analysis"
    _run([sys.executable, skill_root / "scripts" / "analyze.py",
          "--dir", run_dir, "--out", analysis_dir], cwd=skill_root)

    summary_path = analysis_dir / "summary.json"
    if not summary_path.is_file():
        raise SystemExit(f"analyze.py produced no summary.json at {summary_path}")
    summary = json.loads(summary_path.read_text())

    # 5. Campaign-facing result + the small human-readable artifacts.
    results = summarize(summary, spec, args, ranks)
    (out_dir / "results.json").write_text(json.dumps(results, indent=2))
    for extra in ("summary.md", "thermo.png", "order.png"):
        src = analysis_dir / extra
        if src.is_file():
            shutil.copy2(src, out_dir / extra)

    m = results["metrics"]
    print(f"[alloy-thermo-mc] Tc(Cv) = {m['Tc_cv_K']} K, Tc(chi) = {m['Tc_chi_K']} K, "
          f"alpha1 = {m['sro_alpha1']}", flush=True)
    print(f"[alloy-thermo-mc] wrote {out_dir / 'results.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
