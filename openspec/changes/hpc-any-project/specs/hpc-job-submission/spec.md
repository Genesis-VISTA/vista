# Spec Delta

## Purpose

Lets a researcher in any OLCF project with S3M access, or with their own
Perlmutter or Lux account, submit and follow jobs through VISTA using only
their own credentials and a remote folder they choose.

## ADDED Requirements

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
Each researcher SHALL set, per cluster, the folder on that cluster where
VISTA puts job sources and outputs: Odo, Frontier, Perlmutter and Lux each
have their own setting. The system SHALL NOT supply a default or fall back to
a deployment-wide folder. Submission, status and output retrieval on a
cluster whose folder is not set SHALL fail, naming the setting to fill in.
Cancellation SHALL NOT need the folder, since it acts on the job id alone. Researchers
who set the same folder share it; the system SHALL NOT separate their files.

#### Scenario: Folder not set
- **WHEN** a researcher with an Odo S3M token and Globus connection but no Odo remote folder submits a job to Odo
- **THEN** submission fails before any file is moved, saying to set the Odo remote directory in the VISTA user settings

#### Scenario: Two researchers share a folder
- **WHEN** two researchers in the same project set the same Frontier remote folder
- **THEN** both submit jobs into it, and each job's outputs are in that folder's `out/<job id>/`

### Requirement: One folder layout
On every cluster the system SHALL lay out a job under the researcher's remote
folder `<remote_dir>` as follows:
- sources: `<remote_dir>/<job>/src/`
- Slurm stdout and stderr: `<remote_dir>/out/log-<job id>.out` and `.err`
- outputs, exported to the job as `VISTA_OUT`: `<remote_dir>/out/<job id>/`

No part of a path SHALL depend on which run of VISTA submitted the job. The
job's working directory SHALL be `<remote_dir>`, except that Odo keeps
starting job scripts in the job's source folder.

#### Scenario: Same layout on each cluster
- **WHEN** job `example` is submitted to Odo, Frontier, Perlmutter and Lux, each with remote folder `/r`, and receives job id `42`
- **THEN** on each cluster its sources are in `/r/example/src/`, its logs are `/r/out/log-42.out` and `/r/out/log-42.err`, and `VISTA_OUT` is `/r/out/42`

#### Scenario: Submit summary names the paths
- **WHEN** a job is submitted
- **THEN** the tool's result names the job id, the cluster, and the log, stderr and output paths above

### Requirement: Folder permissions on OLCF clusters
Odo and Frontier jobs run as the project's IRI automation user, not as the
researcher, so the system SHALL NOT create any folder the job writes to.
Slurm and the job create `out/` and `out/<job id>/`. Before submitting to Odo
or Frontier the system SHALL check that the remote folder exists and is
writable by its group. If it is missing or not group-writable, submission
SHALL fail with the command that fixes it (`mkdir -p -m 2775 <remote_dir>` or
`chmod 2775 <remote_dir>`). If the folder's permissions cannot be read,
submission SHALL continue. Sources, which the job only reads, SHALL still be
uploaded through the researcher's Globus identity.

#### Scenario: Folder not group-writable
- **WHEN** the Frontier remote folder exists with permissions `0755`
- **THEN** submission fails before the job is submitted, giving `chmod 2775 <remote_dir>`

#### Scenario: Folder missing
- **WHEN** the Odo remote folder does not exist
- **THEN** submission fails, giving `mkdir -p -m 2775 <remote_dir>`

#### Scenario: Output folder is left to Slurm
- **WHEN** a job is submitted to Odo or Frontier with a group-writable remote folder that has no `out/`
- **THEN** VISTA creates no `out/` folder through Globus, and the submission proceeds

#### Scenario: Permissions unreadable
- **WHEN** the listing that would show the remote folder's permissions fails with an error other than an expired Globus session
- **THEN** submission continues, and the job is submitted

### Requirement: Finding a job after submission
Job status, output retrieval and cancellation SHALL identify a job by its id
and cluster alone. The cluster SHALL come from the tool's argument, or else
from the only cluster the researcher has credentials for. The system SHALL
work out the job's log and output paths from its id and the researcher's
current remote folder for that cluster. It SHALL keep no record of submitted
jobs, and SHALL NOT offer a tool that lists them. Changing the remote folder
SHALL make earlier jobs' logs and outputs unreachable through VISTA and SHALL
NOT make status or cancel fail.

#### Scenario: Status after a restart
- **WHEN** a job was submitted to Frontier, the MCP server has restarted since, and status is requested for its id with `cluster="frontier"`
- **THEN** the job's state, log tail, stderr tail and output listing are returned

#### Scenario: Cluster omitted with several configured
- **WHEN** status is requested without a cluster and the researcher has credentials for both Odo and Perlmutter
- **THEN** the call fails, asking for the cluster

#### Scenario: Remote folder changed
- **WHEN** a researcher changes their Odo remote folder after submitting a job there, and then asks for its status
- **THEN** the job's state is still reported, the log and output listing are reported as absent, and the call does not fail

#### Scenario: Dry-run job needs no cluster
- **WHEN** HPC dry-run is on and status is requested for a dry-run job id without a cluster and with no credentials
- **THEN** the dry-run status is returned

#### Scenario: No job listing tool
- **WHEN** an agent's tools are listed
- **THEN** there is no `list_hpc_jobs` tool

### Requirement: Lux account
Lux jobs SHALL be submitted without an `--account` directive, so that Slurm
charges them to the researcher's default account. The system SHALL NOT have a
configured Lux project.

#### Scenario: Lux batch script
- **WHEN** a job is submitted to Lux
- **THEN** its batch script contains no `#SBATCH --account` line

### Requirement: Demo job works in any project
The `example` job SHALL run on Odo and Frontier from any project whose remote
folder is group-writable. It SHALL write only under `VISTA_OUT`, and SHALL
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
