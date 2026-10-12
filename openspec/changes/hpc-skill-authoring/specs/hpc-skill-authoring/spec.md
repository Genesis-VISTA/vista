# Spec Delta

## Purpose

Let a researcher ask the chat agent to make an HPC skill and end with that skill
loaded in their project, without moving files by hand.

## ADDED Requirements

### Requirement: Bundled HPC skill authoring skill

The system SHALL bundle a skill named `hpc-skill-author` that tells the agent how
to write a skill shipping HPC jobs: the folder layout (`SKILL.md`,
`hpc_jobs/<job>/` with `README.md`, `cluster_defaults.json`, `job.<cluster>.slurm`
and optional `setup_<cluster>.sh`), the job environment (`VISTA_OUT`,
`RUN_DIR_<Cluster>`, `VISTA_JOB_DIR`), `script_args` as a `KEY=VALUE` allow-list,
and that a job writes only under `VISTA_OUT` and `VISTA_JOB_DIR`. It SHALL
include templates for a skill and a job, and SHALL tell the agent to write the
new skill to `/mnt/data/output/<skill>/`, where `<skill>` is the new SKILL.md's
`name`.

#### Scenario: A researcher asks for an HPC skill
- **WHEN** a project loads `hpc-skill-author` and the researcher asks the agent to make an HPC skill for a workflow
- **THEN** the agent writes the skill to `/mnt/data/output/<skill>/`, runs the validator on it, and tells the researcher how to import it

### Requirement: Sandbox validator for HPC skills

`hpc-skill-author` SHALL include `scripts/validate.py`, runnable with only the
Python standard library, that checks a skill folder (or a single job folder) and
exits non-zero when it finds an error. It SHALL report as errors at least
everything `submit_hpc_job` refuses for a job (no job script, a missing README or
one not starting `# <job>`, a `cluster_defaults.json` that does not parse into
the cluster defaults, a job script with no section for its cluster, a Lux section
without a partition or GPUs per node, an invalid job name) and everything the
import refuses (no SKILL.md at the root, an invalid SKILL.md `name` or missing
`description`, symlinks, too many or too large files). It SHALL also report as
an error a skill folder whose name differs from its SKILL.md `name`, and a job
the SKILL.md does not name as `<skill>/<job>`. It SHALL report as warnings a job
script that never uses `VISTA_OUT`, and one that reads arguments without a
`KEY=VALUE` allow-list.

Any job folder the validator accepts SHALL load with the same rules
`submit_hpc_job` uses.

#### Scenario: A valid job passes
- **WHEN** the agent runs `validate.py` on a skill made from the bundled templates
- **THEN** it exits 0, and `submit_hpc_job` resolves the job as `<skill>/<job>` once the skill is loaded

#### Scenario: A broken job fails with the rule it breaks
- **WHEN** the job's README starts with a different heading, or its `cluster_defaults.json` has no section for its `job.lux.slurm`
- **THEN** `validate.py` exits non-zero with an error naming the job and the rule

### Requirement: Import a skill from the project's output folder

The system SHALL list, for a project and the requesting user, the top-level
folders of that user's output folder in that project that contain a SKILL.md,
with each one's skill name (or why its SKILL.md is invalid) and the HPC jobs it
ships. The system SHALL import a listed folder as a skill with the same
validation and limits as a folder upload, refusing a folder that contains a
symlink or that resolves outside the output folder. The imported skill SHALL be
private, SHALL be added to the project's skills, and SHALL report its HPC jobs
as any other skill does.

Importing a folder whose skill name is already used SHALL be refused unless the
request asks to replace it and the existing skill is private; a replacement
SHALL keep the existing skill's row and swap its files.

#### Scenario: Importing an agent-written HPC skill
- **WHEN** the agent wrote `/mnt/data/output/lux-hello-skill/` with `hpc_jobs/hello/` and the researcher imports it from the Import modal's "From project output" tab
- **THEN** the skill `lux-hello-skill` is private, is in the project's skills, shows "Runs HPC jobs: hello", and the project's skills volume holds `lux-hello-skill/hpc_jobs/hello/`

#### Scenario: Symlinks are refused
- **WHEN** the output folder holds a symlink, for example to a file outside the sandbox
- **THEN** the import is refused naming the link, and nothing is copied

#### Scenario: Re-importing a fixed skill
- **WHEN** a private skill `lux-hello-skill` exists and the researcher imports `lux-hello-skill` from output again, choosing to replace it
- **THEN** the skill's files are the new folder's, and the skill is still private and in the project

#### Scenario: A published skill cannot be replaced
- **WHEN** the name belongs to a published skill
- **THEN** the import is refused with 409 and the published skill is unchanged
