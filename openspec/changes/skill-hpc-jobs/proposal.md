# Proposal

## Why

`submit_hpc_job` only runs jobs from VISTA's own `hpc_jobs/` folder, read once at
import. A skill that needs a cluster job (its environment setup, job script and
configs) therefore has to be copied into the VISTA repository, so every new HPC
skill is a VISTA change. `lux-primus-skill` (AMD Primus training on OLCF Lux) is
the first such skill: it already ships its job in VISTA's job format, but works
only when copied into `vista/hpc_jobs/`. Its configs are also kept flat, only
because VISTA uploads a job folder's top-level files.

## What Changes

- A loaded skill can ship HPC jobs at `<skill>/hpc_jobs/<job>/`, in the same
  format as VISTA's own jobs. The agent submits one as `job="<skill>/<job>"`.
  VISTA looks the name up per call: first in its own catalog, then among the
  calling project's loaded skills. Skill jobs are checked with the same rules as
  the built-in catalog (job script, README header, `cluster_defaults.json`).
- A job folder's subdirectories are uploaded too, over Globus (Odo, Frontier),
  the NERSC IRI API (Perlmutter) and SFTP (Lux). Orchestration files are still
  left out, but only at the job folder's top level. Dot-files and symlinks are
  never uploaded.
- The `submit_hpc_job` description says that loaded skills may provide more
  jobs, which their SKILL.md documents. An unknown job name lists the jobs the
  project's skills provide.
- The Skills page shows a "Runs HPC jobs" badge on every skill that ships
  jobs, naming them, so a researcher sees that loading an imported skill lets
  the agent run its jobs on a cluster under their credentials.
- Catalog contract tests also cover jobs inside skills, and a hermetic test
  covers the resolver.
- Docs: a "Self-contained HPC skills" section in `docs/hpc-jobs.md` and
  `docs/skill-onboarding.md`.

No breaking change: built-in job names and remote paths are unchanged.

## Capabilities

### New Capabilities
- `skill-hpc-jobs`: HPC jobs shipped inside a skill. Covers naming, lookup
  against the project's loaded skills, validation, upload of nested files, and
  how the UI marks a skill that can run cluster jobs.

### Modified Capabilities
- `hpc-job-contracts`: catalog contract tests also cover jobs found inside
  skills, and a broken skill job fails CI the same way a broken built-in one
  does.

## Impact

- `mcp_servers/vista_mcp_server/src/vista_mcp_server/submit_job_mcp.py`: a job
  resolver, per-job directories instead of `settings.local_hpc_jobs_dir / job`,
  and recursive source sync. `lib/slurm_ssh.py`: upload to nested remote paths.
- `mcp_servers/vista_mcp_server/tests/`: catalog, resolver and upload tests.
- `backend/src/vista_backend/api/skills.py` and `agents/skills.py`: report the
  jobs a skill ships. `ui/app/api/skills/route.ts`, `ui/lib/types.ts`,
  `ui/app/skills/page.tsx`: show the badge.
- `docs/hpc-jobs.md`, `docs/skill-onboarding.md`.
- No new dependencies. The backend already sends `skills_dir` on every MCP
  call.
