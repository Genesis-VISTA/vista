#!/usr/bin/env python3
"""
Vista HPC wrapper for the ``salt-neutronics-tbr`` job.

Turns ONE flat state-point order (a salt composition + a Li-6 enrichment) into a
single tritium-breeding-ratio (TBR) query against the cloned ``salt-neutronics-skill``
(``salt_neutronics``) repo, writing ``results.json`` into the output dir.

This file is the only non-metadata file in ``hpc_jobs/salt-neutronics-tbr/``, so vista
inlines it into the JobSpec, materializing it at ``$RUN_DIR_Odo``.
``job.odo.slurm`` invokes it as::

    python run_state_point.py --skill-root <clone> --output-dir $VISTA_OUT <order...>

The order flags mirror the skill's ``script_args`` contract (see the salt-neutronics-tbr
SKILL.md / the job README). They feed the repo's ``salt_neutronics.cli tbr`` subcommand,
which is run with ``--json --output <output-dir>/results.json``. The bundled Shift
parameter study (``data/neutronics_isotopics.h5``) resolves automatically relative to the
cloned package, so no ``--data`` is needed.
"""
import argparse
import os
import subprocess
import sys


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run one molten-salt neutronics TBR state point.")
    p.add_argument("--skill-root", required=True, help="Path to the cloned salt-neutronics-skill repo.")
    p.add_argument("--output-dir", required=True, help="Where results.json lands (usually $VISTA_OUT).")

    # Composition — exactly one of --bef2 / --be-multiplier.
    p.add_argument("--bef2", type=float, default=None, help="BeF2 content in mol%%.")
    p.add_argument("--be-multiplier", type=float, default=None, dest="be_multiplier",
                   help="Beryllium multiplier (alternative to --bef2).")

    # Conditions / options (forwarded to the tbr query).
    p.add_argument("--li6", type=float, default=None, help="Li-6 enrichment atom fraction (CLI default 0.075).")
    p.add_argument("--nominal-bef2", type=float, default=None, dest="nominal_bef2",
                   help="mol%% BeF2 that maps to beryllium multiplier 1.0 (CLI default 33.33).")
    p.add_argument("--allow-extrapolation", action="store_true",
                   help="Permit (cautious) extrapolation outside the simulated grid.")

    args = p.parse_args(argv)

    if (args.bef2 is None) == (args.be_multiplier is None):
        p.error("provide exactly one composition: --bef2 OR --be-multiplier")

    skill_root = os.path.abspath(args.skill_root)
    out = os.path.abspath(args.output_dir)
    os.makedirs(out, exist_ok=True)
    results = os.path.join(out, "results.json")

    # Make the cloned package importable in place and force headless plotting.
    child_env = {
        **os.environ,
        "PYTHONPATH": skill_root + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "MPLBACKEND": "Agg",
    }

    # The CLI writes the JSON report to --output (and also echoes it to stdout with --json).
    cmd = [sys.executable, "-m", "salt_neutronics.cli", "tbr", "--json", "--output", results]
    if args.bef2 is not None:
        cmd += ["--bef2", str(args.bef2)]
    if args.be_multiplier is not None:
        cmd += ["--be-multiplier", str(args.be_multiplier)]
    if args.li6 is not None:
        cmd += ["--li6", str(args.li6)]
    if args.nominal_bef2 is not None:
        cmd += ["--nominal-bef2", str(args.nominal_bef2)]
    if args.allow_extrapolation:
        cmd += ["--allow-extrapolation"]

    print("[salt-neutronics-tbr] run:", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=skill_root, check=True, env=child_env)

    print(f"[salt-neutronics-tbr] done — results.json in {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
