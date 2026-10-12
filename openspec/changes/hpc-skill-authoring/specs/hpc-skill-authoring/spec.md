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

### Requirement: Wrapping existing code as an HPC skill

`hpc-skill-author` SHALL tell the agent how to turn existing code into an HPC
skill without rewriting it: a repository on github.com or gitlab.com (GitLab
subgroups included), an archive the user uploaded (`.zip`, `.tar`, `.tar.gz`,
`.tgz`), or a folder under `/mnt/data/uploads/`, and SHALL tell it never to
download code from the sandbox.

The agent SHALL have a `fetch_repository(url, ref?)` tool, run by the backend,
that resolves the URL's branch or tag (or the default branch, or `ref`) to the
commit it points at now, downloads the repository by that commit from the
host's API over https, and unpacks it, or the URL's subfolder, into the
agent's own `/mnt/data/work/<name>/` with a `SOURCE.json` recording the clone
URL, ref, commit and whether the repository is private. Only the github.com and
gitlab.com APIs SHALL be requested. When `VISTA_BACKEND_GITHUB_TOKEN` or
`VISTA_BACKEND_GITLAB_TOKEN` is set it SHALL be sent to its own host only, and
SHALL NOT reach the sandbox. Unpacking SHALL keep regular files only and refuse
paths outside the destination. A failure SHALL be returned to the agent as a
retry with the reason, not end the run.

The skill SHALL include `scripts/get_source.py`, standard library only, that
unpacks an uploaded archive or copies an uploaded folder the same way, without
network access, and `scripts/inventory.py`, which lists the code's READMEs,
build files, existing batch scripts with their `#SBATCH` lines, entry points
with their flags, and the environment variables it reads.

It SHALL include a `wrap-repo` job template whose Lux setup script checks a
public repository out at the recorded commit into `$VISTA_JOB_DIR/src/<commit>`,
once, so the skill ships only the wrapper. For an upload, or a private
repository the cluster cannot clone, the agent ships the needed files in the job
folder instead.

#### Scenario: Wrapping a GitHub repository
- **WHEN** the user asks to make an HPC skill from `https://github.com/<owner>/<repo>`
- **THEN** the agent fetches it with `fetch_repository`, writes a skill from the `wrap-repo` template pinned to the resolved commit, the validator passes, and the job resolves as `<skill>/<job>` with only the wrapper files in its folder

#### Scenario: Fetching through a proxy the sandbox does not trust
- **WHEN** the network re-signs HTTPS with a certificate authority only the host trusts, and the agent fetches a repository
- **THEN** the backend downloads it and the sandbox reads it from `/mnt/data/work/<name>/` without making a request

#### Scenario: A private repository
- **WHEN** the repository is private and VISTA has a token for its host
- **THEN** `fetch_repository` downloads it, `SOURCE.json` says `"private": true`, and the token appears nowhere in the agent's workspace

#### Scenario: Wrapping a GitLab repository in a subgroup
- **WHEN** the user asks to make an HPC skill from `https://gitlab.com/<group>/<subgroup>/<repo>`
- **THEN** `fetch_repository` records the project's default branch and the commit it points at, with `https://gitlab.com/<group>/<subgroup>/<repo>.git` as the clone URL

#### Scenario: Another host
- **WHEN** the URL is on any other host, such as a self-hosted GitLab
- **THEN** `fetch_repository` refuses it and the agent asks for an archive

#### Scenario: The pinned commit is checked out once
- **WHEN** a wrapped job's `setup_lux.sh` runs for two submissions while the repository's branch has moved on
- **THEN** both use the recorded commit's checkout, cloned by the first

#### Scenario: A hostile archive
- **WHEN** an uploaded archive has a member named `../x` or an absolute path
- **THEN** `get_source.py` fails and writes nothing outside its destination

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
