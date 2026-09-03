# salt-chemistry-md

NPT molecular-dynamics density of a molten salt via OpenMM + a MACE ML potential,
on OLCF Frontier GPUs. One submission = one state point (one composition at one
temperature) = one GPU. The job clones the public salt-chemistry-skill repo at
runtime, activates a pre-provisioned OpenMM+MACE ROCm conda env, builds the
structure, runs NPT (minimize → equilibrate → produce), and writes `results.json`
into the job's output dir.

This is the HPC backend for the `salt-chemistry-md` skill. No simulation code is
vendored into VISTA — the `saltmd` package, scripts, and the fitted Flibe potential
all come from the clone (https://github.com/jqyin/salt-chemistry-skill, ~10 MB).

Default nodes: 1
Default time: 2:00:00 (raise for long production runs)

## Script args (one flat state-point order)

Passed through to `run_state_point.py`, which routes them to the repo's
`build_structure.py` (composition) and `run_npt.py` (the run):

- Composition (exactly one): `--mol-percent-bef2 P` | `--salt NAME` | `--components "F:p,F:p"`
- `--temperature T`            temperature in K (required)
- `--n-formula-units N`        system size (build default if omitted)
- `--production-steps S`       production MD steps
- `--model PATH`               ML potential; relative paths resolve in the clone
                               (default `assets/mace_flibe.model`, Flibe/Li-Be-F only)
- `--density D`, `--seed N`    initial box density / RNG seed (build)
- `--trajectory`               write the MD trajectory too (off by default — nothing
                               in vista reads it, and everything in `$VISTA_OUT` is
                               uploaded, so on a long run it dominates the transfer)

Example:

    submit_hpc_job(job="salt-chemistry-md", cluster="frontier", duration="2:00:00",
        script_args="--mol-percent-bef2 33.33 --temperature 783.15 "
                    "--n-formula-units 720 --production-steps 250000")

For any non-Flibe salt you MUST pass `--model` with a potential trained on that
chemistry; never run a different salt through the bundled Flibe model.

## Outputs (in $VISTA_OUT)

`results.json` (density at `density.density_g_cm3` ± `density.density_stderr_g_cm3`),
`structure.pdb`, and `equilibration.csv` / production logs.

## One-time Frontier setup (prerequisite, done outside this job)

The job activates a pre-provisioned conda env at `$SALTMD_ENV`
(default `/lustre/orion/proj-shared/chm243/vista/openmm-torch-frontier`). Create it
once at that prefix with the repo's `install_frontier_rocm_stack.sh` (OpenMM[hip] +
openmm-ml + torch + mace-torch on the ROCm stack). Override the location, repo URL,
or ref via `iri.environment` in `cluster_defaults.json` (`SALTMD_ENV`,
`SALTMD_REPO_URL`, `SALTMD_REPO_REF`).
