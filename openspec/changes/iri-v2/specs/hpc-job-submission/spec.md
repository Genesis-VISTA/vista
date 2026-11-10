# Spec Delta

## ADDED Requirements

### Requirement: Each cluster is a named IRI resource
Jobs on every supported cluster SHALL be submitted, queried and cancelled through one
IRI client, at the cluster's facility and configured IRI API version: v2 on S3M's open
enclave for Odo and its moderate enclave for Frontier and Lux, and v1 on NERSC's IRI
service for Perlmutter, whose v2 does not accept the NERSC IRI tokens researchers hold. Each
cluster SHALL be addressed by its configured compute resource id (`odo`, `frontier`, `lux`
and Perlmutter's NERSC compute resource id by default). The system SHALL NOT choose a
resource by discovery, by name matching, or by falling back to another listed resource.
An IRI refusal of the resource (such as 404) SHALL fail the call, naming the cluster and
the resource id, and SHALL NOT be retried against another resource. No OLCF cluster SHALL
use IRI v1, and moving a cluster to another API version SHALL be a configuration change.

#### Scenario: Frontier jobs go to Frontier
- **WHEN** the moderate enclave lists Lux before Frontier and a job is submitted to Frontier
- **THEN** the job is posted to the `frontier` resource, never to `lux`

#### Scenario: Lux jobs go to Lux
- **WHEN** a job is submitted to Lux
- **THEN** it is posted to the moderate enclave's `lux` resource with the researcher's Lux S3M token

#### Scenario: Perlmutter jobs go to Perlmutter's compute resource
- **WHEN** a job is submitted to Perlmutter
- **THEN** it is posted to NERSC's `/api/v1/compute/job/<Perlmutter compute id>` with the researcher's NERSC IRI token, and not to the `login` or `jobs` resource NERSC also lists

#### Scenario: Unknown resource
- **WHEN** the IRI service answers 404 for the configured resource id
- **THEN** the submission fails naming the cluster and resource id, and no other resource is tried

#### Scenario: Job id is the scheduler's
- **WHEN** a submission succeeds
- **THEN** the job id reported to the agent is the id IRI returned, and status, outputs and cancellation accept it

### Requirement: Job states from IRI
The system SHALL report a job's state as IRI gives it (such as QUEUED, RUNNING,
COMPLETED, FAILED, CANCELED), with its exit code only once the job has ended, and its
message only when IRI gives a real one. A cancellation SHALL be reported as requested when
IRI accepts it, whatever state the job then reports.

#### Scenario: A failed job's exit code
- **WHEN** a Lux job's script exits with status 7
- **THEN** status reports state FAILED with exit code 7

#### Scenario: Queued job has no exit code
- **WHEN** IRI reports a queued job with exit code 0 and message `None`
- **THEN** status reports state QUEUED with no exit code and no message

#### Scenario: Cancellation accepted
- **WHEN** a running Lux job is cancelled and IRI answers 204
- **THEN** the tool reports cancellation requested for that job on Lux

### Requirement: Job environment on OLCF clusters
Every Odo, Frontier and Lux job SHALL run its cluster's setup script, when the job ships
one (`setup_<cluster>.sh`), inside the allocation before the job script, and both SHALL
see the job's environment: `VISTA_OUT`, `VISTA_JOB_DIR`, `RUN_DIR_<Cluster>`, the job's
`cluster_defaults.json` environment, and the OLCF HTTP proxy. `HOME` SHALL default to
`VISTA_OUT` when the scheduler leaves it unset. A setup script that fails SHALL fail the
job, with its output in the job's logs. On Lux, the job's `cluster_defaults.json`
`partition` SHALL be passed as the IRI queue, and `gpus_per_node` as GPUs per process
with one process per node unless the job sets processes per node. A job whose
`cluster_defaults.json` sets `iri.pre_launch` SHALL be refused, since the setup script
replaces it, and a `gpus_per_node` that is not a multiple of the processes per node SHALL
be refused, since IRI asks for GPUs per process. A Lux job SHALL keep the modules Lux
starts it with, which its job scripts load from, rather than starting from `module purge`.

#### Scenario: Lux setup runs in the allocation
- **WHEN** a Lux job with a `setup_lux.sh` is submitted
- **THEN** nothing runs on a login node, and the setup script runs on the job's compute node before `job.lux.slurm`, with the proxy and `RUN_DIR_Lux` set

#### Scenario: Lux partition and GPUs
- **WHEN** a Lux job's `cluster_defaults.json` has `"partition": "batch"` and `"gpus_per_node": 1`
- **THEN** the IRI job is submitted with queue `batch` and one GPU per process

#### Scenario: A stale pre_launch
- **WHEN** a job's `cluster_defaults.json` sets `iri.pre_launch`
- **THEN** the submission is refused with a message saying to move it into `setup_<cluster>.sh`, and no job is submitted

#### Scenario: Lux keeps its default modules
- **WHEN** a Lux job's environment script runs `module load mpich/5.0.1`
- **THEN** the module is found, because the job was not started from `module purge`

#### Scenario: HOME is set
- **WHEN** a Lux job starts with no `HOME` from the scheduler
- **THEN** `HOME` is the job's `VISTA_OUT`


### Requirement: Output folder on Perlmutter
Perlmutter jobs run as the researcher, so VISTA can create their log folder itself.
Before submitting, the system SHALL create `<remote_dir>/out` on Perlmutter through the
NERSC IRI filesystem API, since upstream Slurm does not create a missing log folder.

#### Scenario: Perlmutter creates its output folder
- **WHEN** a job is submitted to Perlmutter with remote folder `/r`
- **THEN** `/r/out` is created through IRI before the job is submitted

### Requirement: Perlmutter files through IRI filesystem tasks
Perlmutter's source upload, output folder creation, log tails, output listing and output
downloads SHALL go through NERSC's IRI filesystem API on its configured storage
resource. Each operation is a task; the system SHALL wait for it to finish within a bound,
and SHALL report a task that failed with the task's own reason and the path, a path the
facility reports missing as missing (as a not-yet-written log, not as an error), and a task
that did not finish in time as unfinished, never as an empty result. A downloaded file
SHALL be saved exactly as the facility returned it; a file the API cannot return intact
SHALL fail naming the file, and SHALL NOT be saved altered.

#### Scenario: Log not written yet
- **WHEN** status is requested for a running Perlmutter job whose log file does not exist yet
- **THEN** the state is reported with "(no logs yet)", and the call does not fail

#### Scenario: Queued job
- **WHEN** status is requested for a queued Perlmutter job
- **THEN** the state is reported from IRI alone, and no filesystem task is started

#### Scenario: Task fails
- **WHEN** the upload task for a source file finishes as `failed` with a permission error
- **THEN** submission fails naming the file and the facility's reason, and no job is submitted

#### Scenario: Task never finishes
- **WHEN** a listing task is still `active` when the wait bound is reached
- **THEN** the call reports the listing as unfinished, not as an empty output folder

## MODIFIED Requirements

### Requirement: Any OLCF project
For Odo, Frontier and Lux the system SHALL accept an S3M token from any OLCF
project. It SHALL learn the token's project from S3M introspection and SHALL
charge the job to that project as its Slurm account. It SHALL NOT compare the
token's project with any configured project or allow-list, and SHALL NOT
require an account setting for Odo, Frontier or Lux.

#### Scenario: Token for a project VISTA has never seen
- **WHEN** a researcher whose Frontier S3M token belongs to project `abc123` submits a job to Frontier
- **THEN** the job is submitted with Slurm account `abc123`

#### Scenario: Lux charges its token's project
- **WHEN** a researcher whose Lux S3M token belongs to project `xyz789` submits a job to Lux
- **THEN** the job is submitted with Slurm account `xyz789`, and no Lux account setting is read

#### Scenario: No Lux token
- **WHEN** a researcher with no Lux S3M token submits a job to Lux
- **THEN** submission fails before any file is moved, saying to add a Lux S3M token in the VISTA user settings

#### Scenario: Introspection refuses the token
- **WHEN** S3M introspection answers the token with an error status
- **THEN** submission fails before any file is moved, saying the token may be expired or invalid and to mint a new one in the VISTA user settings

#### Scenario: Introspection gives no project
- **WHEN** introspection succeeds but reports no project for the token
- **THEN** submission fails, saying the token's project could not be determined

### Requirement: Researcher's own Globus credential
Odo, Frontier and Lux file operations SHALL be authorized only by Globus tokens the
researcher connected in the VISTA user settings: that cluster's own pair
first, then the researcher's pair shared by all clusters. Frontier and Lux SHALL
share one own pair, since both reach the same filesystem through the same collection:
connecting Globus from either cluster's settings SHALL connect both, and disconnecting
from either SHALL disconnect both. A pair SHALL count
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

#### Scenario: Lux uses Frontier's connection
- **WHEN** the researcher connected Globus from Frontier's settings and submits a job to Lux
- **THEN** Lux file operations use that pair, on the Frontier collection, and the researcher is not asked to connect Globus again

### Requirement: One folder layout
On the OLCF clusters (Odo, Frontier and Lux) the system SHALL lay out a job in
folders beside the researcher's remote folder `<remote_dir>`:
- sources: `<remote_dir>.<user>.jobs/<job>/src/`, where `<user>` is the
  researcher's account on that cluster -- the owner Globus reports for their
  home folder
- Slurm stdout and stderr: `<remote_dir>.out/log-<job id>.out` and `.err`
- outputs, exported to the job as `VISTA_OUT`: `<remote_dir>.out/<job id>/`
- state a job's runs share and write (a checkout, a downloaded model), exported
  as `VISTA_JOB_DIR` and as the default of each `FORGE_MODEL_<cluster>`
  (`<remote_dir>.out/<job>/model`): `<remote_dir>.out/<job>/`

On Odo, Frontier and Lux, files the researcher uploads outside a job (see
hpc-file-transfer, Uploading local files) SHALL go in
`<remote_dir>.<user>.jobs/transfers/`, created through the researcher's Globus
identity like the sources beside it, so that a job can read them.

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
sources folder and the project's IRI automation user creates the output folder,
and neither can write in a folder the other created.
This is a temporary workaround until S3M supports IRI filesystem operations.

#### Scenario: Same layout on each OLCF cluster
- **WHEN** researcher `jdoe` submits job `example` to Odo, Frontier and Lux, each with remote folder `/r`, and it receives job id `42`
- **THEN** on each its sources are in `/r.jdoe.jobs/example/src/`, its logs are `/r.out/log-42.out` and `/r.out/log-42.err`, `VISTA_OUT` is `/r.out/42`, and `VISTA_JOB_DIR` is `/r.out/example`

#### Scenario: Uploads sit beside the sources
- **WHEN** researcher `jdoe`, with Odo remote folder `/r`, uploads `salt.xyz` to destination `inputs`
- **THEN** it is at `/r.jdoe.jobs/transfers/inputs/salt.xyz`, and a job submitted to Odo by `jdoe` can read it at that path

#### Scenario: Perlmutter keeps one folder
- **WHEN** job `example` is submitted to Perlmutter with remote folder `/r` and receives job id `42`
- **THEN** its sources are in `/r/jobs/example/src/`, its logs are `/r/out/log-42.out` and `.err`, and `VISTA_OUT` is `/r/out/42`

#### Scenario: Submit summary names the paths
- **WHEN** a job is submitted
- **THEN** the tool's result names the job id, the cluster, and the log, stderr and output paths above

### Requirement: Folder permissions on OLCF clusters
Odo, Frontier and Lux jobs run as the project's IRI automation user, not as the
researcher. The system SHALL NOT create `<remote_dir>.out` or anything in it:
Slurm creates the log folder as the job's user, and the job creates
`<job id>/`. So the automation user must be able to create
`<remote_dir>.out`, which needs the folder containing `<remote_dir>` to be
writable by the project's group. Before submitting to Odo, Frontier or Lux the
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
group, because it is shared by identities that differ (the automation user, the
researcher who ran jobs on Lux over SSH before, colleagues): `umask 002`, then a
best-effort `chgrp` to the project and `chmod 2775` of `<remote_dir>.out`, which
succeed only for the folder's owner and are otherwise ignored. Perlmutter jobs
SHALL NOT change permissions: their folder belongs to the researcher alone.

#### Scenario: A new folder in a group-writable parent
- **WHEN** the Frontier remote folder is `/lustre/orion/abc123/proj-shared/foo`, neither `foo.<user>.jobs` nor `foo.out` exists, and `proj-shared` has permissions `0770`
- **THEN** submission proceeds with no setup, and VISTA creates no folder under `foo.out` through Globus

#### Scenario: A parent the group cannot write
- **WHEN** the Odo remote folder's parent has permissions `0755` and `<remote_dir>.out` does not exist
- **THEN** submission fails before the job is submitted, giving `mkdir -p -m 2775 <remote_dir>.out`

#### Scenario: Lux is checked like Frontier
- **WHEN** the Lux remote folder's parent has permissions `0755` and `<remote_dir>.out` does not exist
- **THEN** submission to Lux fails before the job is submitted, giving `mkdir -p -m 2775 <remote_dir>.out`

#### Scenario: A parent that does not exist
- **WHEN** the folder containing the Odo remote folder does not exist
- **THEN** submission fails, giving `mkdir -p -m 2775 <remote_dir>.out`

#### Scenario: The output folder already exists
- **WHEN** `<remote_dir>.out` exists, whatever its permissions
- **THEN** submission proceeds

#### Scenario: Permissions unreadable
- **WHEN** the listing that would show the parent's permissions fails with an error other than an expired Globus session or a missing path
- **THEN** submission continues, and the job is submitted

### Requirement: Finding a job after submission
Job status, output retrieval and cancellation SHALL identify a job by its id
and cluster alone, and the cluster SHALL be a required argument of each. It
SHALL NOT be inferred: job ids are unique only within a cluster, and Frontier and
Lux share one enclave and filesystem, so a guess could reach a different job with
the same id -- possibly a colleague's, since every project job runs as the same
automation user. The system SHALL
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

### Requirement: Hermetic coverage
Submission, status, outputs and cancel SHALL be tested for every cluster
against faked S3M, IRI and Globus responses with no network access.
Those tests SHALL use project names that no deployment is configured for.

#### Scenario: PR CI
- **WHEN** PR CI runs
- **THEN** these tests pass with no real credentials, and any live test is marked `live` or `hpc` and excluded

### Requirement: Expired Globus session on file operations
An Odo, Frontier or Lux file operation -- one made while submitting a job, polling
its status or retrieving its outputs, or by a transfer tool listing,
downloading or uploading (see hpc-file-transfer) -- that meets a Globus refusal
reconnecting fixes SHALL fail saying the researcher's Globus session for that
cluster has expired and to reconnect Globus for it in the VISTA user settings,
and SHALL name the login Globus requires when it names one, leaving out
Globus's own app-client domain (`clients.auth.globus.org`), which is not a
login a researcher can use. This SHALL hold however Globus phrases the refusal:
a 401, a refusal asking the researcher to authenticate again whatever its HTTP
status, or a refresh token Globus will no longer exchange. Such a refusal SHALL
NOT be reported as a missing file, an empty folder, or a generic refusal to
serve the file.

#### Scenario: Expired session over HTTPS
- **WHEN** a job-log poll asks for a file's size on Odo and the collection answers 403, code `InsufficientSession`, asking for a login with opensso.ccs.ornl.gov
- **THEN** the operation fails saying the Globus session for Odo has expired and to reconnect it in the VISTA user settings, says Globus needs a login with opensso.ccs.ornl.gov, does not mention clients.auth.globus.org, and does not say Globus refused to serve the file

#### Scenario: Expired session on a listing
- **WHEN** a Frontier directory listing fails with HTTP 502, code `ExternalError.DirListingFailed.LoginFailed`, asking for a new session
- **THEN** the operation fails saying the Globus session for Frontier has expired and to reconnect it

#### Scenario: Expired session on a Lux job
- **WHEN** fetching a Lux job's outputs meets a Globus refusal asking for a new session
- **THEN** the operation fails saying the Globus session for Lux has expired and to reconnect it, and does not report the output folder as empty

#### Scenario: Expired session on a transfer tool
- **WHEN** an agent lists, downloads or uploads on Odo through a transfer tool and Globus asks for a new session
- **THEN** the call fails saying the Globus session for Odo has expired and to reconnect it, and reports no file as missing and no folder as empty

#### Scenario: Missing file is still a missing file
- **WHEN** the collection answers 404 for the requested path
- **THEN** the operation reports that the path is not on the cluster, not an expired session

## REMOVED Requirements

### Requirement: Output folder on Perlmutter and Lux
**Reason**: Lux jobs now run as the project's IRI automation user, so VISTA can no longer create Lux's output folder itself; Lux follows "Folder permissions on OLCF clusters" instead.
**Migration**: Perlmutter's behavior is unchanged and restated in "Output folder on Perlmutter".

### Requirement: Lux account
**Reason**: Lux jobs are charged to the Lux S3M token's project, as on Odo and Frontier.
**Migration**: Researchers add a Lux S3M token in Settings. The saved `lux_account` value is left in the database, unused.
