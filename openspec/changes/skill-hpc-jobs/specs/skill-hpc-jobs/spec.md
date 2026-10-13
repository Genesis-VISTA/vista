# Spec Delta

## Purpose

Let a skill bring its own HPC jobs (job script, setup script, configs), so a new
cluster workflow ships as a skill and needs no change to VISTA.

## ADDED Requirements

### Requirement: Jobs inside a skill

A skill MAY contain HPC jobs at `hpc_jobs/<job>/` inside its folder, in the same
format as VISTA's built-in jobs: at least one cluster job script, a `README.md`
whose first line is `# <job>`, and an optional `cluster_defaults.json`. The
system SHALL name such a job `<skill>/<job>`, where `<skill>` is the skill's
folder name in the project, and SHALL accept that name in `submit_hpc_job`.

The system SHALL look a job name up on every call: first among the built-in
jobs, then, for a name of the form `<skill>/<job>`, among the skills loaded in
the calling project. A skill that is not loaded in the calling project SHALL NOT
provide jobs. A name with any part that is empty, starts with `.`, or contains
anything other than letters, digits, `.`, `_` and `-` SHALL be refused, and so
SHALL a name with more than one `/`. The job folder SHALL resolve to a path
inside the project's skills folder.

#### Scenario: Submitting a job a loaded skill ships
- **WHEN** a project has the skill `primus-on-lux` loaded, the skill contains `hpc_jobs/primus-smoke/` with a `job.lux.slurm`, and the agent calls `submit_hpc_job(job="primus-on-lux/primus-smoke", cluster="lux")`
- **THEN** the job is submitted from the skill's files, with nothing copied into VISTA's `hpc_jobs/`

#### Scenario: A skill that is not loaded provides nothing
- **WHEN** the agent submits `primus-on-lux/primus-smoke` in a project that does not load `primus-on-lux`
- **THEN** the call fails, saying the job is not recognized

#### Scenario: Path tricks are refused
- **WHEN** the agent submits `../other/x`, `a/b/c`, or `skill/.hidden`
- **THEN** the call fails without reading anything outside the project's skills folder

#### Scenario: Built-in names are unchanged
- **WHEN** the agent submits a built-in job such as `example`
- **THEN** it resolves to VISTA's own `hpc_jobs/example/`, exactly as before

### Requirement: Skill jobs are checked like built-in jobs

The system SHALL check a skill job with the same rules as a built-in job before
using it: a job script for at least one cluster, a `README.md` that starts with
`# <job>`, and a `cluster_defaults.json` that parses when present. A skill job
that fails a check SHALL fail the submission with a message naming the skill,
the job and the broken rule.

#### Scenario: Broken skill job
- **WHEN** the agent submits `myskill/myjob` and `myjob/README.md` starts with `# other`
- **THEN** the call fails, naming `myskill/myjob` and the README header rule

### Requirement: Discovering skill jobs

The `submit_hpc_job` description SHALL say that loaded skills may provide more
jobs, named `<skill>/<job>` and documented in the skill's SKILL.md. When a
submission names an unknown job, the error SHALL list the built-in jobs and the
jobs the calling project's loaded skills provide.

#### Scenario: Unknown job lists skill jobs
- **WHEN** a project loads `primus-on-lux` and the agent submits `primus-smoke`
- **THEN** the error lists `primus-on-lux/primus-smoke` among the available jobs

### Requirement: Remote paths for skill jobs

A skill job SHALL use the remote layout of a built-in job with the job name
`<skill>/<job>` in place of `<job>`. On OLCF its sources are in
`<remote_dir>.<user>.jobs/<skill>/<job>/src/` and `VISTA_JOB_DIR` is
`<remote_dir>.out/<skill>/<job>`; on Perlmutter the same structure is inside
`<remote_dir>`.

#### Scenario: Skill job on Lux
- **WHEN** researcher `jdoe` submits `primus-on-lux/primus-smoke` to Lux with remote folder `/r`
- **THEN** its sources are in `/r.jdoe.jobs/primus-on-lux/primus-smoke/src/` and `VISTA_JOB_DIR` is `/r.out/primus-on-lux/primus-smoke`

### Requirement: Nested job files are uploaded

The system SHALL upload every regular file under a job folder, keeping its path
relative to the folder, to the job's sources folder on every cluster. It SHALL
leave out the orchestration files (`README.md`, `cluster_defaults.json` and the
cluster job and setup scripts) only at the job folder's top level, and SHALL
never upload a file or folder whose name starts with `.`, a `__pycache__`
folder, or a symlink. The rule that skips an upload when every file is already
on the cluster at its full length SHALL apply to nested files too.

#### Scenario: Configs in a subfolder
- **WHEN** a job folder holds `configs/model.yaml` and `README.md`
- **THEN** `configs/model.yaml` is uploaded to `<src>/configs/model.yaml` and `README.md` is not uploaded

#### Scenario: A nested README is a source
- **WHEN** a job folder holds `docs/README.md`
- **THEN** it is uploaded, since only the top-level `README.md` describes the job to VISTA

#### Scenario: Symlinks stay local
- **WHEN** a job folder holds a symlink to a file outside it
- **THEN** the symlink is not uploaded

### Requirement: Skills that run cluster jobs are marked

A skill that ships HPC jobs can make the agent run commands on a cluster as the
researcher. The Skills page SHALL show a "Runs HPC jobs" badge on every such
skill, naming its jobs, whether the skill was imported or written locally. The
skills API SHALL report the jobs each skill ships.

#### Scenario: Imported skill with a job
- **WHEN** a researcher imports a skill from GitHub whose folder contains `hpc_jobs/primus-smoke/`
- **THEN** the Skills page shows the skill with a "Runs HPC jobs" badge that names `primus-smoke`

#### Scenario: Ordinary skill
- **WHEN** a skill has no `hpc_jobs/` folder
- **THEN** no badge is shown
