# Design

## Context

`submit_job_mcp.py` builds `AVAILABLE_JOBS` once at import from
`settings.local_hpc_jobs_dir`, and every dispatcher and source sync recomputes
`settings.local_hpc_jobs_dir / job`. The backend already sends
`project_paths.skills_dir` on every MCP call: the project's skills volume
(`data/volumes/<agent>/skills/`), which holds a copy of exactly the skills the
project loads, each in a folder named after the skill. Source upload lists only
a job folder's top-level files, on all three transports.

## Goals / Non-Goals

**Goals:**
- One function that turns a job name into its folder and parsed metadata, used
  by every dispatcher.
- Nested job files on every transport, with the existing "skip when every file
  is already there at full length" rule.

**Non-Goals:**
- Per-call approval of skill jobs. The trust decision below explains why a badge
  is the first step, and what a stronger gate would look like.
- Skill jobs in campaign manifests beyond what already works: a campaign's
  `submit_hpc_job` call passes the same `skills_dir`, so a manifest naming
  `<skill>/<job>` works without changes.
- Changing the built-in catalog's format or remote paths.

## Decisions

**Lookup per call, built-ins first.** `resolve_job(job, skills_dir)` returns a
`JobInfo` that now carries the job's local `dir`. A name without `/` is looked up
in `AVAILABLE_JOBS`, so built-in behaviour and the static tool description stay
as they are. A `<skill>/<job>` name is loaded from
`<skills_dir>/<skill>/hpc_jobs/<job>/` with the same `_load_job` function that
`get_available_jobs()` now uses. Alternative: rescan all skills on every call
and merge into one dict. Rejected because a broken job in an unrelated skill
would then break every submission; loading only the named job keeps a bad skill
local to itself. Skills are not cached: the volume is rebuilt when the project's
skills change, and reading one README and one JSON per submit costs nothing next
to a cluster round trip.

**Name `<skill>/<job>`, kept as a path.** The qualified name goes into the
remote layout unchanged, so the sources are at `.../<skill>/<job>/src` and
shared state at `.out/<skill>/<job>`. Every mkdir in the dispatchers is already
`mkdir -p`, so the extra level costs nothing. Alternative: flatten to
`<skill>--<job>`. Rejected: it is a second spelling of the same name to keep in
sync with docs, and the nested path reads the same as the tool argument. The
Slurm job name becomes `vista-<skill>/<job>`, which Slurm accepts.

**Strict name check before touching disk.** Each part must match
`[A-Za-z0-9][A-Za-z0-9._-]*`, there is exactly one `/`, and the resolved folder
must be inside `skills_dir`. This refuses `..`, absolute paths and hidden
folders without depending on the filesystem.

**Dispatchers resolve by name.** `_submit_<cluster>_job(..., job, ...)` keeps its
signature and gains an optional `job_info`. Without one it resolves `job` against
the built-ins. Existing tests that call the dispatchers directly keep working,
and `submit_hpc_job` passes the resolved `JobInfo`. The sync helpers take the
local folder (`Path`) rather than a job name, because they only need the folder.

**One upload set, three transports.** `_job_sources(dir)` walks the folder and
returns `(relative POSIX path, Path)` pairs. It skips the top-level
orchestration files, anything named `.*` or `__pycache__` at any depth, and
symlinks. The symlink rule matters for skills: they are copied with
`symlinks=True`, and a link could point at anything on the host.
- Globus: list the remote folder recursively only when the set has nested
  files. A flat job keeps today's single non-recursive `ls`, whose 404 on a
  missing folder already means "upload everything". Compare sizes by relative
  path, `mkdir -p` each needed parent, then `PUT` each stale file.
- SFTP: `list_file_sizes` gains a recursive mode (a walk of SFTP `readdir` calls, one per folder, only for a nested job)
  and `upload_files` takes remote relative paths, creating parents first.
- Perlmutter IRI: unchanged skip rule (any entry means populated), plus `mkdir`
  for each nested parent before `upload`.

**Trust: mark the skill, keep the existing gates.** A skill job runs only when
(1) the skill is loaded in the project, (2) the agent calls `submit_hpc_job`,
which is already a destructive, open-world tool, and (3) the researcher has
connected that cluster's credential (Lux also asks for an SSH login once per
session). What is new is that an imported GitHub skill, not just VISTA's
reviewed catalog, can supply the script. So the Skills page badges every skill
that ships jobs ("Runs HPC jobs: primus-smoke"), computed from the skill folder
on disk, so it cannot be hidden by frontmatter. The proposed next step, which is
not part of this change, is a one-time "allow this skill to run cluster jobs"
approval per project, enforced by the backend leaving
`<skill>/hpc_jobs` out of the volume until approved.

## Risks / Trade-offs

- [A skill job's script runs with the researcher's cluster identity] → badge on
  the Skills page; documented in
  `docs/skill-onboarding.md`; an approval gate proposed as a follow-up.
- [Large nested folders slow the first submit, one PUT per file] → documented:
  ship sources and configs only, and build or download big artifacts on the
  cluster (as `setup_lux.sh` does). The recursive Globus walk keeps its
  `max_dirs` cap and prunes `.git`, `.venv` and `__pycache__`.
- [Two skills with the same folder name] → impossible inside one project's
  volume (one folder per skill name).
