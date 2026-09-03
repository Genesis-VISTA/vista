# salt-neutronics-tbr

Tritium breeding ratio (TBR) of a FLiBe (LiF–BeF₂) molten-salt breeding blanket,
emulated by interpolating a precomputed Shift Monte Carlo parameter study. It ships CPU
backends for **Odo** (the OLCF open enclave) and **Perlmutter** (NERSC) — pick with
`cluster=`. One submission = one state point (one composition at one Li-6 enrichment) =
one `results.json`. The job clones the public salt-neutronics-skill repo at runtime, uses
a tiny Python env (numpy/scipy/h5py/matplotlib), runs the `tbr` query, and writes
`results.json` into the job's output dir. The clone, the env, and all caches live in
**node-local/scratch** workspace (off `$VISTA_OUT`), so the only thing in `$VISTA_OUT` is
`results.json` — this keeps the post-run `get_hpc_job_status` output listing to a single
call instead of walking hundreds of venv/`.git` subdirs.

This is the HPC backend for the `salt-neutronics-tbr` skill. No analysis code is
vendored into VISTA — the `salt_neutronics` package and the bundled Shift data table
(`data/neutronics_isotopics.h5`, ~59 KB) all come from the clone
(https://github.com/jqyin/salt-neutronics-skill).

The calculation is **CPU-only, single-core, sub-second**: no GPU, no pre-provisioned
conda env. The HPC path exists so a multi-agent campaign can run one state point per
subagent through VISTA's HPC backend, symmetrically with the GPU `salt-chemistry-md`
density sim — the two are the natural pair for screening breeder salts.

Default nodes: 1
Default time: 0:10:00 (clone + venv build + sub-second run)

## Script args (one flat state-point order)

Passed through to `run_state_point.py`, which routes them to the repo's
`salt_neutronics.cli tbr` subcommand:

- Composition (exactly one): `--bef2 P` (mol% BeF₂) | `--be-multiplier M`
- `--li6 E`                 Li-6 enrichment atom fraction (default 0.075 ≈ natural)
- `--nominal-bef2 P`        mol% BeF₂ that maps to beryllium multiplier 1.0 (default 33.33)
- `--allow-extrapolation`   permit (cautious) extrapolation outside the scanned grid

Valid ranges (single-point queries outside are rejected unless `--allow-extrapolation`):
BeF₂ 30–46.67 mol%, Li-6 enrichment 0.07–1.0.

Example:

    submit_hpc_job(job="salt-neutronics-tbr", cluster="odo", duration="0:10:00",
        script_args="--bef2 33.33 --li6 0.075")
    # cluster="perlmutter" works identically (NERSC CPU partition).

## Outputs (in $VISTA_OUT)

`results.json` — the TBR report. TBR at `result.tbr`; `provenance.is_extrapolated`
flags out-of-grid; `input_composition` + `derived.beryllium_multiplier` echo the state
point. See `references/data_schema.md` in the repo for the full schema.

## Prerequisites

Just the standard credentials for whichever cluster you target — for **Odo**, an Odo S3M
token (the deployment supplies the S3 output-push credential); for **Perlmutter**, a NERSC IRI token
plus `nersc_account` / `nersc_remote_dir` in the user settings. No pre-provisioned env is
needed on either. The repo URL / ref can be overridden per cluster via `iri.environment`
in `cluster_defaults.json` (`SALTN_REPO_URL`, `SALTN_REPO_REF`).
