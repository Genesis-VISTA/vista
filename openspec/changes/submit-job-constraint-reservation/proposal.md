# Proposal

## Why

Hackathon feedback (#86) asked for "S3M support for -C nvme and --reservation".
Today `submit_hpc_job` has neither: each job has one fixed constraint in its
`cluster_defaults.json`, and reservations are not supported at all.

A spike against the live OLCF IRI services found that the reservation reaches
Slurm on both Odo and Frontier, since a fake one is rejected with "Requested
reservation is invalid". It also found that a constraint never does. OLCF's IRI
layer copies `custom_attributes` keys into the Slurm job (its submit response
shows `job.constraints: "nvme"`). Even so, Slurm records `constraints: ""`, a
nonexistent feature is accepted, and an `nvme` job on Frontier gets no NVMe.
This happens on IRI v1 and v2, with `constraint` and `constraints`. The key
VISTA sends is the one NERSC's, IRI v2's and OLCF's own published schemas
document (`{"constraint": "gpu"}`), so the loss is on OLCF's side, below the IRI
layer. VISTA already sends a constraint from `cluster_defaults.json` there, and
it is silently dropped.

## What Changes

- `submit_hpc_job` takes two optional arguments, `constraint` and
  `reservation`, on every cluster. A constraint replaces the job's default
  constraint and is not combined with it. A reservation is per call only and is
  never read from `cluster_defaults.json`.
- Both values are checked against Slurm's syntax before anything is submitted,
  dry runs included. Anything else is refused, so on Lux no value can add
  `#SBATCH` lines or shell.
- On Odo and Frontier, the system reads Slurm's record of the job right after
  submitting it. If a requested constraint or reservation is missing, it
  cancels the job and refuses, naming the cause. If the record cannot be read,
  the job is kept and the summary says the request is unverified. Once OLCF
  forwards constraints the check passes, and nothing in VISTA needs to change.
- The submit summary reports the effective constraint and reservation.
- Perlmutter stops sending a `custom_attributes` constraint of `null` for jobs
  without one.
- Campaign subagents can pass both arguments. The Hypothesis Lab simulation
  keeps submitting with the job's defaults.
- The tool description and `docs/hpc-jobs.md` gain the two arguments, and the
  HPC skill author's `#SBATCH` mapping gains `-C` and `--reservation`. The
  molten-salt system prompt's out-of-date `submit_hpc_job` signature is
  corrected.

No breaking change. A job that sets no constraint and passes no reservation is
submitted exactly as before. An Odo or Frontier job whose defaults set a
constraint is now cancelled instead of running unconstrained, but no bundled
job sets one.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `hpc-job-submission`: adds the requirement "Per-submission constraint and
  reservation", covering the arguments, their validation, how each cluster
  passes them to Slurm, and the Odo/Frontier post-submit check.

## Impact

- `mcp_servers/vista_mcp_server`:
  - `submit_job_mcp.py`: tool signature, description, validation, the
    per-cluster submit functions, the summary and the post-submit check.
  - `lib/slurm_ssh.py`: `--reservation`.
  - `lib/iri.py`: possibly a helper to read Slurm's record.
  - Tests: `test_submit_job_spec.py`, `test_lux_submit.py`, the fake IRI client,
    and a new SDK conversion test.
- `backend`:
  - `agents/campaign/hpc_tools.py` and `subagent.py` forward the arguments.
  - The fake MCP tool signature in `tests/harness/fake_mcp.py`.
  - `db/system_prompts/molten-salt.md` and `db/skills/hpc-skill-author/SKILL.md`.
- Docs: `docs/hpc-jobs.md`.
- No new dependencies, no database change, no UI change. The approval modal
  already shows raw tool arguments.
- Outside VISTA: OLCF is asked whether S3M can forward `constraints`. That
  isn't a task in this change.
