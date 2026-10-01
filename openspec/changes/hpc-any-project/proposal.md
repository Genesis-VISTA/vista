# Proposal

## Why

VISTA's job submission only works for the OLCF projects it was built for:
`gen150-vista` on Odo, `chm243` on Frontier and `stf218` on Lux. It refuses any
other project's S3M token and writes to those projects' folders. The project
check was added when file transfer could fall back to one shared Globus
identity. Now that each researcher connects their own Globus account and the
facility enforces what they can read and write, the check no longer protects
anything. It only stops other projects from using VISTA.

## What Changes

- **BREAKING** Odo and Frontier accept an S3M token from any OLCF project. The
  token's project becomes the job's Slurm account. The "Wrong project"
  credential state is removed; the HPC card shows the token's project instead.
- **BREAKING** Only a researcher's own Globus tokens authorize Odo and Frontier
  file operations. The deployment-wide Globus fallback
  (`VISTA_MCP_{ODO,FRONTIER}_GLOBUS_*REFRESH_TOKEN`) is removed.
- **BREAKING** The remote folder is a required per-user setting for Odo,
  Frontier and Lux, as it already is for Perlmutter. The deployment defaults
  (`*_remote_dir`, `lux_account`) are removed. Lux jobs no longer pass
  `--account`, so Slurm uses the researcher's default account.
- Every cluster uses one folder layout beside the researcher's remote folder:
  sources in `<remote_dir>.jobs/<job>/src/` (uploaded through the researcher's
  Globus identity), Slurm logs and outputs in `<remote_dir>.out/`
  (`log-<id>.out`/`.err`, and `<id>/` as `VISTA_OUT`). The per-server-run
  `session_id` folder is removed.
- **BREAKING** Because every path follows from the job id and the remote
  folder, the job registry (`data/hpc_job_registry.json`) and the
  `list_hpc_jobs` tool are removed. Status, outputs and cancel work for any job
  in the researcher's remote folder, whichever run of VISTA submitted it.
- VISTA creates nothing the job writes through Globus. On Odo and Frontier,
  Slurm creates `<remote_dir>.out` as the project's IRI automation user, so the
  folder containing `<remote_dir>` must be writable by the project's group, and
  VISTA checks this before submitting. OLCF's `proj-shared` is group-writable
  (770), so a new
  remote folder directly under it needs no setup. The sibling folders are a
  temporary workaround until S3M supports IRI filesystem operations, which
  would let VISTA create one folder as the automation user.
- The `example` job runs from any project.

## Non-goals

- PALISADE / VISTAGuard, including `g5_allocation_policy.json`: off by
  default, and not blocking.
- Making project-specific jobs (`forge-tune`, `salt-chemistry-md`, ...)
  generic. That is a separate change.
- Perlmutter's settings, which are already per user.

## Capabilities

### New Capabilities

- `hpc-job-submission`: which project a job runs under, where its files live
  on the cluster, which credentials authorize it, and how status and outputs
  find a job afterwards, for Odo, Frontier, Perlmutter and Lux.

### Modified Capabilities

- `hpc-availability`: the credential check no longer compares the token's
  project with a configured account, and the Wrong project state goes. The
  Globus check no longer counts a deployment-wide credential. Lux's check no
  longer names a configured project. The settings modal gains remote folder
  fields for Odo, Frontier and Lux.

## Impact

- Code: the vista MCP server's job tools and settings; the backend's HPC
  settings, status service, user schema (new `odo_remote_dir` and
  `lux_remote_dir` columns) and agent tool list; the UI settings modal and HPC
  cards; `scripts/get_globus_token.py` and the package launchers; README,
  AGENTS.md and `hpc_jobs/example`. See design.md and tasks.md for files.
- Deployments: a hosted server that relied on the shared Globus login stops
  moving files until each researcher connects Globus. Every researcher must
  set a remote folder for each cluster they use. Logs and outputs of jobs
  submitted before the upgrade can no longer be fetched through VISTA.
