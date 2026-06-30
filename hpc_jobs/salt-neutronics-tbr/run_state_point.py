#!/usr/bin/env python3
"""
Vista HPC wrapper for the ``salt-neutronics-tbr`` job.

Turns ONE flat state-point order (a salt composition + a Li-6 enrichment) into a
single tritium-breeding-ratio (TBR) query against the cloned ``salt-neutronics-skill``
(``salt_neutronics``) repo, writing ``results.json`` into the output dir.

This file is the only non-metadata file in ``hpc_jobs/salt-neutronics-tbr/``, so vista
Globus-stages it to ``$RUN_DIR_Odo``. ``job.odo.slurm`` invokes it as::

    python run_state_point.py --skill-root <clone> --output-dir $VISTA_OUT <order...>

The ``<order...>`` is the campaign's ``script_args`` forwarded verbatim. The campaign
planner now passes the candidate as a **single JSON token** (see
``campaign.planner.encode_candidate_args``), e.g.::

    python run_state_point.py --skill-root … --output-dir … '{"bef2":40,"li6_enrichment":0.7}'

so this wrapper accepts that JSON object (positional ``candidate``) and maps its keys onto
the query. The explicit ``--bef2``/``--li6``/… flags still work for direct/manual calls and
take precedence over the JSON. Keys the v1 neutronics grid does not model (temperature,
blanket thickness, beryllium concentration) are reported and ignored. The query feeds the
repo's ``salt_neutronics.cli tbr`` subcommand, run with ``--json --output
<output-dir>/results.json``; the bundled Shift parameter study
(``data/neutronics_isotopics.h5``) resolves automatically relative to the cloned package,
so no ``--data`` is needed.
"""
import argparse
import json
import os
import subprocess
import sys


# Candidate JSON keys this neutronics state point understands -> the argparse dest they fill.
# Synonyms map to the same dest; the v1 Shift grid is indexed by BeF2 mol% (or the equivalent
# beryllium multiplier) and Li-6 enrichment only.
_FLOAT_KEY_ALIASES = {
    "bef2": "bef2",
    "bef2_mol_percent": "bef2",
    "be_multiplier": "be_multiplier",
    "beryllium_multiplier": "be_multiplier",
    "li6": "li6",
    "li6_enrichment": "li6",
    "nominal_bef2": "nominal_bef2",
}
_FLAG_KEY_ALIASES = {"allow_extrapolation": "allow_extrapolation"}


def apply_candidate(args: argparse.Namespace, candidate: dict) -> list[str]:
    """Merge a candidate dict onto `args`, filling only values not already set by a flag.

    Returns the list of candidate keys this neutronics sim does not model (so the caller can
    report them); explicit CLI flags always win over the JSON.
    """
    ignored: list[str] = []
    for key, value in candidate.items():
        dest = _FLOAT_KEY_ALIASES.get(key)
        if dest is not None:
            if getattr(args, dest) is None:
                setattr(args, dest, float(value))
            continue
        flag_dest = _FLAG_KEY_ALIASES.get(key)
        if flag_dest is not None:
            if value:
                setattr(args, flag_dest, True)
            continue
        ignored.append(key)
    return ignored


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run one molten-salt neutronics TBR state point.")
    p.add_argument("--skill-root", required=True, help="Path to the cloned salt-neutronics-skill repo.")
    p.add_argument("--output-dir", required=True, help="Where results.json lands (usually $VISTA_OUT).")

    # Composition — at most one of --bef2 / --be-multiplier (defaults to eutectic FLiBe if neither).
    p.add_argument("--bef2", type=float, default=None, help="BeF2 content in mol%%.")
    p.add_argument("--be-multiplier", type=float, default=None, dest="be_multiplier",
                   help="Beryllium multiplier (alternative to --bef2).")

    # Conditions / options (forwarded to the tbr query).
    p.add_argument("--li6", type=float, default=None, help="Li-6 enrichment atom fraction (CLI default 0.075).")
    p.add_argument("--nominal-bef2", type=float, default=None, dest="nominal_bef2",
                   help="mol%% BeF2 that maps to beryllium multiplier 1.0 (CLI default 33.33).")
    p.add_argument("--allow-extrapolation", action="store_true",
                   help="Permit (cautious) extrapolation outside the simulated grid.")

    # The campaign candidate, as a single JSON object (the campaign wire format).
    p.add_argument("candidate", nargs="?", default=None,
                   help="Candidate as one JSON object, e.g. '{\"bef2\":40,\"li6_enrichment\":0.7}'.")

    args = p.parse_args(argv)

    if args.candidate:
        try:
            candidate = json.loads(args.candidate)
        except json.JSONDecodeError as exc:
            p.error(f"candidate is not valid JSON: {exc}")
        if not isinstance(candidate, dict):
            p.error("candidate JSON must be an object")
        ignored = apply_candidate(args, candidate)
        if ignored:
            print("[salt-neutronics-tbr] ignoring keys not modeled by the neutronics grid: "
                  f"{', '.join(sorted(ignored))}", flush=True)

    if args.bef2 is not None and args.be_multiplier is not None:
        p.error("provide at most one composition: --bef2 OR --be-multiplier")
    if args.bef2 is None and args.be_multiplier is None:
        # No composition given (e.g. the candidate only varied Li-6): use eutectic FLiBe,
        # which the skill defines as beryllium multiplier 1.0 (33.33 mol% BeF2).
        args.be_multiplier = 1.0
        print("[salt-neutronics-tbr] no composition in order; defaulting to eutectic FLiBe "
              "(beryllium multiplier 1.0).", flush=True)

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
