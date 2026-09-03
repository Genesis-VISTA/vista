#!/usr/bin/env python3
"""
Vista HPC wrapper for the ``salt-chemistry-md`` job.

Turns ONE flat state-point order (composition + temperature + run options) into a
structure build and an NPT density run using the cloned ``salt-chemistry-skill``
(``saltmd``) repo, writing ``results.json`` into the output dir.

This file is the only non-metadata file in ``hpc_jobs/salt-chemistry-md/``, so vista
inlines it into the JobSpec, materializing it at ``$RUN_DIR_Frontier``.
``job.frontier.slurm`` invokes it as::

    python run_state_point.py --skill-root <clone> --output-dir $VISTA_OUT <order...>

The order flags mirror the skill's ``script_args`` contract (see the salt-chemistry-md
SKILL.md / the job README). Composition/build flags feed ``scripts/build_structure.py``;
run flags feed ``scripts/run_npt.py``. Both repo scripts are invoked with ``cwd`` set to
the clone root so ``saltmd`` imports and the default ``assets/`` model path resolve.
"""
import argparse
import os
import subprocess
import sys
import tempfile


def _child_home() -> str:
    """
    A writable HOME for the build/run children: not the user's real one, and not
    the output dir.

    Under vista's IRI dispatch the inherited HOME drags
    ~/.local/lib/pythonX/site-packages onto sys.path, which shadows the conda
    env's torch so torch.cuda.is_available() is False ("Torch reports no GPU").
    Exporting HOME in the slurm shell does not reliably reach this spawned
    run_npt.py, so it is set directly in the child env (PYTHONNOUSERSITE is what
    actually keeps user-site off sys.path).

    It used to point at --output-dir, which fixed the import problem but put
    pip/conda/matplotlib dotfile trees inside $VISTA_OUT — uploaded verbatim,
    as many small objects. $VISTA_SCRATCH is the directory the job contract
    reserves for exactly this and deletes on exit; outside vista (a standalone
    run) a temp dir serves the same purpose.
    """
    scratch = os.environ.get("VISTA_SCRATCH")
    home = os.path.join(scratch, "run-home") if scratch else tempfile.mkdtemp(
        prefix="salt-chemistry-md-home-"
    )
    os.makedirs(home, exist_ok=True)
    return home


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Build + NPT-run one molten-salt state point.")
    p.add_argument("--skill-root", required=True, help="Path to the cloned salt-chemistry-skill repo.")
    p.add_argument("--output-dir", required=True, help="Where results.json / structure.pdb land (usually $VISTA_OUT).")

    # Composition — exactly one (feeds build_structure.py).
    p.add_argument("--mol-percent-bef2", type=float, default=None, help="Flibe shortcut: mol%% BeF2.")
    p.add_argument("--salt", default=None, help="Named preset (flibe, flinak, licl-kcl, ...).")
    p.add_argument("--components", default=None, help="Explicit 'FORMULA:MOLEPCT,...' mixture.")

    # Build options.
    p.add_argument("--n-formula-units", type=int, default=None)
    p.add_argument("--density", type=float, default=None)
    p.add_argument("--jitter-fraction", type=float, default=None)
    p.add_argument("--min-distance", type=float, default=None)

    # Run options (feed run_npt.py).
    p.add_argument("--temperature", type=float, required=True, help="Temperature in K.")
    p.add_argument("--pressure", type=float, default=None)
    p.add_argument("--production-steps", type=int, default=None)
    p.add_argument("--equilibration-steps", type=int, default=None)
    p.add_argument("--report-interval", type=int, default=None)
    p.add_argument("--model", default="assets/mace_flibe.model",
                   help="ML potential; relative paths resolve against --skill-root.")
    p.add_argument("--platform", default=None, help="OpenMM platform (default from env OPENMM_PLATFORM).")
    p.add_argument("--precision", default=None)
    p.add_argument(
        "--trajectory", action=argparse.BooleanOptionalAction, default=False,
        help="Write the MD trajectory into --output-dir. Off by default: nothing in "
             "vista reads it and $VISTA_OUT is uploaded verbatim, so for a long "
             "production run it is the largest thing in the upload by far.",
    )

    # Shared.
    p.add_argument("--seed", type=int, default=None)

    args = p.parse_args(argv)

    chosen = [c for c in (args.mol_percent_bef2 is not None, bool(args.salt), bool(args.components)) if c]
    if len(chosen) != 1:
        p.error("provide exactly one composition: --mol-percent-bef2 | --salt | --components")

    skill_root = os.path.abspath(args.skill_root)
    out = os.path.abspath(args.output_dir)
    os.makedirs(out, exist_ok=True)
    py = sys.executable
    structure = os.path.join(out, "structure.pdb")

    child_env = {**os.environ, "HOME": _child_home(), "PYTHONNOUSERSITE": "1"}

    # ---- build the periodic box ----
    build = [py, os.path.join(skill_root, "scripts", "build_structure.py")]
    if args.mol_percent_bef2 is not None:
        build += ["--mol-percent-bef2", str(args.mol_percent_bef2)]
    if args.salt:
        build += ["--salt", args.salt]
    if args.components:
        build += ["--components", args.components]
    if args.n_formula_units is not None:
        build += ["--n-formula-units", str(args.n_formula_units)]
    if args.density is not None:
        build += ["--density", str(args.density)]
    if args.jitter_fraction is not None:
        build += ["--jitter-fraction", str(args.jitter_fraction)]
    if args.min_distance is not None:
        build += ["--min-distance", str(args.min_distance)]
    if args.seed is not None:
        build += ["--seed", str(args.seed)]
    build += ["-o", structure]
    print("[salt-chemistry-md] build:", " ".join(build), flush=True)
    subprocess.run(build, cwd=skill_root, check=True, env=child_env)

    # ---- resolve the ML potential (relative paths live in the clone) ----
    model = args.model if os.path.isabs(args.model) else os.path.join(skill_root, args.model)
    if not os.path.exists(model):
        sys.exit(
            f"[salt-chemistry-md] ERROR: model not found: {model}\n"
            "The bundled potential is Flibe-only (Li/Be/F). For another salt pass "
            "--model with a potential trained on that chemistry."
        )

    # ---- run the NPT density calculation ----
    run = [
        py, os.path.join(skill_root, "scripts", "run_npt.py"),
        "--structure", structure,
        "--model", model,
        "--temperature", str(args.temperature),
        "--output-dir", out,
    ]
    if args.pressure is not None:
        run += ["--pressure", str(args.pressure)]
    if args.production_steps is not None:
        run += ["--production-steps", str(args.production_steps)]
    if args.equilibration_steps is not None:
        run += ["--equilibration-steps", str(args.equilibration_steps)]
    if args.report_interval is not None:
        run += ["--report-interval", str(args.report_interval)]
    if args.platform:
        run += ["--platform", args.platform]
    if args.precision:
        run += ["--precision", args.precision]
    if not args.trajectory:
        run += ["--no-trajectory"]
    if args.seed is not None:
        run += ["--seed", str(args.seed)]
    print("[salt-chemistry-md] run:", " ".join(run), flush=True)
    subprocess.run(run, cwd=skill_root, check=True, env=child_env)

    print(f"[salt-chemistry-md] done — results.json in {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
