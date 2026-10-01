# hpc-job-submission Specification

## Purpose
Lets a researcher in any OLCF project with S3M access, or with their own
Perlmutter or Lux account, submit and follow jobs through VISTA using only
their own credentials and a remote folder they choose.

## Requirements

### Requirement: Any OLCF project
For Odo and Frontier the system SHALL accept an S3M token from any OLCF
project. It SHALL learn the token's project from S3M introspection and SHALL
charge the job to that project as its Slurm account. It SHALL NOT compare the
token's project with any configured project or allow-list, and SHALL NOT
require an account setting for Odo or Frontier.

#### Scenario: Token for a project VISTA has never seen
- **WHEN** a researcher whose Frontier S3M token belongs to project `abc123` submits a job to Frontier
- **THEN** the job is submitted with Slurm account `abc123`

#### Scenario: Introspection refuses the token
- **WHEN** S3M introspection answers the token with an error status
- **THEN** submission fails before any file is moved, saying the token may be expired or invalid and to mint a new one in the VISTA user settings

#### Scenario: Introspection gives no project
- **WHEN** introspection succeeds but reports no project for the token
- **THEN** submission fails, saying the token's project could not be determined

### Requirement: Researcher's own Globus credential
Odo and Frontier file operations SHALL be authorized only by Globus tokens the
researcher connected in the VISTA user settings: that cluster's own pair
first, then the researcher's pair shared by both clusters. A pair SHALL count
only when both its Transfer and collection tokens are present. The system
SHALL NOT use any deployment-wide Globus credential, and SHALL NOT read one
from the environment.

#### Scenario: Deployment variable is ignored
- **WHEN** `VISTA_MCP_ODO_GLOBUS_REFRESH_TOKEN` and `VISTA_MCP_ODO_GLOBUS_HTTPS_REFRESH_TOKEN` are set in the environment and the researcher has connected no Globus account
- **THEN** an Odo submission fails, saying to connect Globus for Odo in the VISTA user settings

#### Scenario: Shared pair is used
- **WHEN** the researcher has no Frontier-specific Globus pair but has a complete shared pair
- **THEN** Frontier file operations use the shared pair

#### Scenario: Half a pair is skipped
- **WHEN** the researcher's Odo pair has a Transfer token but no collection token, and their shared pair is complete
- **THEN** Odo file operations use the shared pair

### Requirement: Remote folder per researcher
Each researcher SHALL set, per cluster, the folder on that cluster that names
where VISTA puts job sources and outputs (see One folder layout): Odo,
Frontier, Perlmutter and Lux each have their own setting. The system SHALL NOT
supply a default or fall back to a deployment-wide folder. Submission, status
and output retrieval on a cluster whose folder is not set SHALL fail, naming
the setting to fill in. Cancellation SHALL NOT need the folder, since it acts
on the job id alone.

On the OLCF clusters, researchers in one project who set the same folder SHALL
be able to share it: each uploads sources to their own folder, and all their
jobs write logs and outputs to the one shared output folder. A researcher MAY
also give Frontier and Lux the same folder.

#### Scenario: Folder not set
- **WHEN** a researcher with an Odo S3M token and Globus connection but no Odo remote folder submits a job to Odo
- **THEN** submission fails before any file is moved, saying to set the Odo remote directory in the VISTA user settings

#### Scenario: Two researchers share a folder
- **WHEN** researchers `jdoe` and `asmith` in the same project set the same Frontier remote folder `/r` and both submit `example`
- **THEN** `jdoe`'s sources are in `/r.jdoe.jobs/` and `asmith`'s in `/r.asmith.jobs/`, both submissions succeed, and each job's outputs are in `/r.out/<job id>/`

#### Scenario: Frontier and Lux share a folder
- **WHEN** a researcher sets the same remote folder for Frontier and Lux and submits to both, in either order
- **THEN** both jobs can write their logs and outputs to `<remote_dir>.out`

### Requirement: One folder layout
On the OLCF clusters (Odo, Frontier and Lux) the system SHALL lay out a job in
folders beside the researcher's remote folder `<remote_dir>`:
- sources: `<remote_dir>.<user>.jobs/<job>/src/`, where `<user>` is the
  researcher's account on that cluster -- the owner Globus reports for their
  home folder on Odo and Frontier, and their SSH login on Lux
- Slurm stdout and stderr: `<remote_dir>.out/log-<job id>.out` and `.err`
- outputs, exported to the job as `VISTA_OUT`: `<remote_dir>.out/<job id>/`
- state a job's runs share and write (a checkout, a downloaded model), exported
  as `VISTA_JOB_DIR` and as the default of each `FORGE_MODEL_<cluster>`
  (`<remote_dir>.out/<job>/model`): `<remote_dir>.out/<job>/`

`<remote_dir>` itself SHALL NOT be created on OLCF. On Perlmutter the system
SHALL use the same structure inside `<remote_dir>` instead:
`<remote_dir>/jobs/<job>/src/` and `<remote_dir>/out/...`, since Perlmutter
jobs run as the researcher and the NERSC IRI filesystem API lets VISTA manage
the folder itself.

No part of a log, output or shared-state path SHALL depend on which run of
VISTA, or which researcher, submitted the job. The job's working directory
SHALL be its cluster's sources folder (`<remote_dir>.<user>.jobs` or
`<remote_dir>/jobs`), except that Odo keeps starting job scripts in the job's
source folder.

On OLCF the folders are separate because the researcher's identity creates the
sources folder and, on Odo and Frontier, the project's IRI automation user
creates the output folder, and neither can write in a folder the other created.
This is a temporary workaround until S3M supports IRI filesystem operations.

#### Scenario: Same layout on each OLCF cluster
- **WHEN** researcher `jdoe` submits job `example` to Odo, Frontier and Lux, each with remote folder `/r`, and it receives job id `42`
- **THEN** on each its sources are in `/r.jdoe.jobs/example/src/`, its logs are `/r.out/log-42.out` and `/r.out/log-42.err`, `VISTA_OUT` is `/r.out/42`, and `VISTA_JOB_DIR` is `/r.out/example`

#### Scenario: Perlmutter keeps one folder
- **WHEN** job `example` is submitted to Perlmutter with remote folder `/r` and receives job id `42`
- **THEN** its sources are in `/r/jobs/example/src/`, its logs are `/r/out/log-42.out` and `.err`, and `VISTA_OUT` is `/r/out/42`

#### Scenario: Submit summary names the paths
- **WHEN** a job is submitted
- **THEN** the tool's result names the job id, the cluster, and the log, stderr and output paths above

### Requirement: Folder permissions on OLCF clusters
Odo and Frontier jobs run as the project's IRI automation user, not as the
researcher. The system SHALL NOT create `<remote_dir>.out` or anything in it:
Slurm creates the log folder as the job's user, and the job creates
`<job id>/`. So the automation user must be able to create
`<remote_dir>.out`, which needs the folder containing `<remote_dir>` to be
writable by the project's group. Before submitting to Odo or Frontier the
system SHALL check:
- if `<remote_dir>.out` exists, submission SHALL continue, since an earlier job
  or the researcher made it;
- otherwise, if the folder containing `<remote_dir>` is missing or not
  writable by its group, submission SHALL fail, naming the one command that
  fixes it: `mkdir -p -m 2775 <remote_dir>.out`;
- if the permissions cannot be read, submission SHALL continue.

Sources, which the job only reads, SHALL still be uploaded through the
researcher's Globus identity.

Every OLCF job SHALL begin by keeping the output folder writable by the project's
group, because it is shared by identities that differ (the automation user,
the researcher on Lux, colleagues): `umask 002`, then a best-effort `chgrp` to
the project and `chmod 2775` of `<remote_dir>.out`, which succeed only for the
folder's owner and are otherwise ignored. On Lux, VISTA SHALL do the same when
it creates the folder. Perlmutter jobs SHALL NOT change permissions: their
folder belongs to the researcher alone.

#### Scenario: A new folder in a group-writable parent
- **WHEN** the Frontier remote folder is `/lustre/orion/abc123/proj-shared/foo`, neither `foo.<user>.jobs` nor `foo.out` exists, and `proj-shared` has permissions `0770`
- **THEN** submission proceeds with no setup, and VISTA creates no folder under `foo.out` through Globus

#### Scenario: A parent the group cannot write
- **WHEN** the Odo remote folder's parent has permissions `0755` and `<remote_dir>.out` does not exist
- **THEN** submission fails before the job is submitted, giving `mkdir -p -m 2775 <remote_dir>.out`

#### Scenario: A parent that does not exist
- **WHEN** the folder containing the Odo remote folder does not exist
- **THEN** submission fails, giving `mkdir -p -m 2775 <remote_dir>.out`

#### Scenario: The output folder already exists
- **WHEN** `<remote_dir>.out` exists, whatever its permissions
- **THEN** submission proceeds

#### Scenario: Permissions unreadable
- **WHEN** the listing that would show the parent's permissions fails with an error other than an expired Globus session or a missing path
- **THEN** submission continues, and the job is submitted

### Requirement: Output folder on Perlmutter and Lux
Perlmutter and Lux jobs run as the researcher, so VISTA can create their log
folder itself. Before submitting, the system SHALL create the output folder:
`<remote_dir>/out` on Perlmutter through the NERSC IRI filesystem API, and
`<remote_dir>.out` on Lux over the researcher's SSH connection. Only Odo's and
Frontier's Slurm have been seen to create a missing log folder; upstream Slurm
does not.

#### Scenario: Perlmutter creates its output folder
- **WHEN** a job is submitted to Perlmutter with remote folder `/r`
- **THEN** `/r/out` is created through IRI before the job is submitted

#### Scenario: Lux creates its output folder
- **WHEN** a job is submitted to Lux with remote folder `/r`
- **THEN** `/r.out` is created over SSH, group-writable, before `sbatch` runs

### Requirement: Finding a job after submission
Job status, output retrieval and cancellation SHALL identify a job by its id
and cluster alone, and the cluster SHALL be a required argument of each. It
SHALL NOT be inferred: job ids are unique only within a cluster, and Lux, which
needs no token, could never be the inferred one, so a guess could reach a
different job with the same id -- possibly a colleague's, since every project
job runs as the same automation user. The system SHALL
work out the job's log and output paths from its id and the researcher's
current remote folder for that cluster. It SHALL keep no record of submitted
jobs, and SHALL NOT offer a tool that lists them. Changing the remote folder
SHALL make earlier jobs' logs and outputs unreachable through VISTA and SHALL
NOT make status or cancel fail.

#### Scenario: Status after a restart
- **WHEN** a job was submitted to Frontier, the MCP server has restarted since, and status is requested for its id with `cluster="frontier"`
- **THEN** the job's state, log tail, stderr tail and output listing are returned

#### Scenario: Cluster omitted
- **WHEN** status, outputs or cancel is requested without a cluster, even by a researcher with credentials for one cluster only
- **THEN** the call is rejected for the missing argument, and no cluster is contacted

#### Scenario: Remote folder changed
- **WHEN** a researcher changes their Odo remote folder after submitting a job there, and then asks for its status
- **THEN** the job's state is still reported, the log and output listing are reported as absent, and the call does not fail

#### Scenario: Dry-run job needs no credentials
- **WHEN** HPC dry-run is on and status is requested for a dry-run job id, with its cluster but no credentials
- **THEN** the dry-run status is returned

#### Scenario: No job listing tool
- **WHEN** an agent's tools are listed
- **THEN** there is no `list_hpc_jobs` tool

### Requirement: Lux account
Lux has no token to take a project from, so each researcher SHALL set a Lux
account: the OLCF project Lux jobs are charged to. Lux jobs SHALL be submitted
with `#SBATCH -A <lux account>`. Submission to Lux without one SHALL fail,
naming the setting, and an account that is not a plain project name (letters,
digits, `_`, `-`) SHALL be refused.

#### Scenario: Lux batch script
- **WHEN** a researcher with Lux account `abc123` submits a job to Lux
- **THEN** its batch script contains `#SBATCH -A abc123`

#### Scenario: No Lux account
- **WHEN** a researcher with no Lux account submits a job to Lux
- **THEN** submission fails before anyone is asked to log in, saying to set the Lux account

### Requirement: Demo job works in any project
The `example` job SHALL run on Odo and Frontier from any project whose remote
folder sits in a group-writable folder. It SHALL write only under `VISTA_OUT`, and SHALL
depend on no path outside the researcher's remote folder except modules the
cluster provides to every user.

#### Scenario: Example writes only to its output folder
- **WHEN** the `example` job's Odo and Frontier scripts are read
- **THEN** every file they create, including a Python virtual environment, is under `$VISTA_OUT`

### Requirement: Hermetic coverage
Submission, status, outputs and cancel SHALL be tested for every cluster
against faked S3M, IRI, Globus and SSH responses with no network access.
Those tests SHALL use project names that no deployment is configured for.

#### Scenario: PR CI
- **WHEN** PR CI runs
- **THEN** these tests pass with no real credentials, and any live test is marked `live` or `hpc` and excluded
