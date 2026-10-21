## MODIFIED Requirements

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

On Odo and Frontier, files the researcher uploads outside a job (see
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
sources folder and, on Odo and Frontier, the project's IRI automation user
creates the output folder, and neither can write in a folder the other created.
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

### Requirement: Expired Globus session on file operations
An Odo or Frontier file operation -- one made while submitting a job, polling
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

#### Scenario: Expired session on a transfer tool
- **WHEN** an agent lists, downloads or uploads on Odo through a transfer tool and Globus asks for a new session
- **THEN** the call fails saying the Globus session for Odo has expired and to reconnect it, and reports no file as missing and no folder as empty

#### Scenario: Missing file is still a missing file
- **WHEN** the collection answers 404 for the requested path
- **THEN** the operation reports that the path is not on the cluster, not an expired session
