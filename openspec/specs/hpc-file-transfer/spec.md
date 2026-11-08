# hpc-file-transfer Specification

## Purpose
Lets an agent list, fetch and place files on Odo and Frontier outside any job, acting only as
the researcher's own Globus identity, so that data a job needs or produced elsewhere can reach
the sandbox and the cluster.

## Requirements

### Requirement: Transfer tools
The system SHALL offer agents three tools for Odo and Frontier: one that lists a remote path,
one that downloads remote files, and one that uploads local files. Each SHALL take the cluster
as a required argument whose only accepted values are `odo` and `frontier`. Each SHALL be
authorized only by the researcher's Globus credential for that cluster (see
hpc-job-submission, Researcher's own Globus credential), and SHALL need no S3M token. The
system SHALL hand each transfer tool call the researcher's settings and credentials, as it
does for the job tools, and SHALL NOT hand them to tools that are neither. The tool
descriptions SHALL say to leave bulk data on the cluster for a job to process there, that
uploads land only in the researcher's `transfers/` folder, that the researcher approves
each upload and download, and that a download fetches a file again by itself when it changed on
the cluster, so a local copy need never be deleted to refresh it.

#### Scenario: Another cluster is refused
- **WHEN** an agent calls any transfer tool with `cluster="perlmutter"` or `cluster="lux"`
- **THEN** the call is rejected for an invalid argument, and no cluster is contacted

#### Scenario: Globus not connected
- **WHEN** a researcher with no Globus connection for Frontier, and no shared one, calls a transfer tool for Frontier
- **THEN** the call fails, saying to connect Globus for Frontier in the VISTA user settings

#### Scenario: A connected researcher's call carries the connection
- **WHEN** a researcher who has connected Globus for Odo asks an agent to list an Odo folder
- **THEN** the call uses their Odo Globus credential, and does not fail saying Globus is not connected

#### Scenario: Present in every project
- **WHEN** an agent's tools are listed in a project with no skills attached
- **THEN** the three transfer tools are among them

### Requirement: Listing a remote path
The list tool SHALL take an absolute remote path and SHALL return, for each entry under it, its
name, whether it is a file or a folder, its size, and its absolute path. It SHALL list the
entries' contents too when asked to recurse. A recursive listing SHALL NOT look inside a folder
below the listed path whose name starts with `.` or is `__pycache__`; it SHALL still list that
folder, marked as not walked. Any path the researcher's identity can read SHALL be listable;
the system SHALL add no limit of its own on where. A relative path SHALL be refused without
contacting the cluster.

#### Scenario: A project data folder
- **WHEN** an agent lists `/lustre/orion/abc123/proj-shared/data` on Frontier, which holds `a.nc` (2 GB) and folder `runs/`
- **THEN** the result names `a.nc` as a file of 2 GB at `/lustre/orion/abc123/proj-shared/data/a.nc` and `runs` as a folder

#### Scenario: Hidden folders in a job's output
- **WHEN** an agent recursively lists a job's output folder on Odo that holds `chart.png`, `.cache/pip/...` and `.local/...`
- **THEN** the result lists `chart.png`, `.cache` and `.local`, marks `.cache` and `.local` as not walked, and lists nothing inside them

#### Scenario: Asking for a hidden folder by name
- **WHEN** an agent recursively lists `<job output>/.cache` itself
- **THEN** its contents are listed

#### Scenario: Relative path
- **WHEN** an agent lists `data/runs` on Odo
- **THEN** the call fails, saying the path must be absolute, and no cluster is contacted

#### Scenario: Missing path
- **WHEN** the listed path does not exist on the cluster
- **THEN** the call reports that the path is not on the cluster, not an empty folder

#### Scenario: Missing path, listed recursively
- **WHEN** an agent recursively lists a path that does not exist on Odo
- **THEN** the call reports that the path is not on Odo, not an empty folder

### Requirement: Downloading remote files
The download tool SHALL take a list of absolute remote paths and SHALL save each file in the
project's output folder at `transfers/<cluster>/<remote path without its leading slash>`,
reporting each one's sandbox path (`/mnt/data/output/transfers/...`), so that the sandbox and
file display can open it. The report SHALL say, for each file, whether it was downloaded by
this call or was already up to date and not fetched. Any file the researcher's identity can
read, hidden or not, SHALL be downloadable, and the system SHALL impose no size limit. A path
that is not on the cluster SHALL be reported as missing while the other files are still
downloaded. A path given more than once SHALL be treated as one file. A relative path, one
containing `..`, or `/` itself SHALL be refused without contacting the cluster. An interrupted
download SHALL NOT leave a partial file at the reported path.

A downloaded file SHALL keep the remote file's modification time. A file already saved there,
not empty, and matching the remote file's size and modification time (to the second) SHALL NOT
be fetched again. One that differs in either SHALL be fetched again, replacing the local copy.
When the cluster reports no modification time, the size alone SHALL decide.

When the cluster cannot be asked whether files changed (Globus not connected, or its session
expired), a call whose files are all held locally SHALL report their sandbox paths with a
warning naming the cluster and the reason, and SHALL NOT ask for approval. A call with any file
not held locally SHALL fail as it would without the local copies.

#### Scenario: Fetch a dataset file
- **WHEN** an agent downloads `/lustre/orion/abc123/proj-shared/data/a.nc` from Frontier
- **THEN** the file is saved at `/mnt/data/output/transfers/frontier/lustre/orion/abc123/proj-shared/data/a.nc`, and that path is reported
- **AND** the saved file's modification time is the remote file's

#### Scenario: Already downloaded
- **WHEN** the same file is downloaded a second time, the first copy is still there, and the remote file is unchanged
- **THEN** it is not fetched from the cluster again, and the same sandbox path is reported as already up to date

#### Scenario: Changed on the cluster
- **WHEN** a file is downloaded again after the remote file was rewritten, with the same size but a later modification time
- **THEN** it is fetched again, after approval, the local copy holds the new contents, and it is reported as downloaded

#### Scenario: Edited locally
- **WHEN** a downloaded file's local copy was edited in the sandbox and it is downloaded again
- **THEN** the remote file is fetched again, after approval, replacing the edited copy

#### Scenario: Cannot check, all local
- **WHEN** a researcher whose Odo Globus session has expired downloads a file already held locally
- **THEN** its sandbox path is reported with a warning that Odo could not be checked for a newer version because the session expired

#### Scenario: Cannot check, one not local
- **WHEN** a researcher whose Odo Globus session has expired downloads two files and one is not held locally
- **THEN** the call fails saying the Globus session expired, not that a file is missing

#### Scenario: One file missing
- **WHEN** an agent downloads two files from Odo and the first does not exist there
- **THEN** the result reports the first as not on the cluster and the second as downloaded

#### Scenario: Escape attempt
- **WHEN** an agent downloads `/lustre/../etc/passwd`
- **THEN** the call fails before contacting the cluster, and nothing is written locally

#### Scenario: The root is not a file
- **WHEN** an agent downloads `/` from Odo
- **THEN** the call fails before contacting the cluster, and nothing is written locally

### Requirement: Uploading local files
The upload tool SHALL take a list of local files and a destination folder relative to the
researcher's `transfers/` folder on that cluster (see hpc-job-submission, One folder layout),
and SHALL upload each file there under its own name, creating any missing folders below
`transfers/`. It SHALL report each file's absolute remote path. It SHALL NOT write anywhere
else on the cluster: an absolute destination, or one containing `..` or a backslash, SHALL be
refused without contacting the cluster. A file of the same name already in the destination
SHALL be replaced. Two local files with the same name in one call SHALL be refused before
anything is uploaded, since one would replace the other.

Local files SHALL be given as sandbox paths under `/mnt/data/output` or `/mnt/data/uploads`;
any other path, one that leaves those folders, a folder, or a missing file SHALL be refused
before anything is uploaded. Uploading SHALL need the researcher's remote folder for that
cluster, and SHALL fail naming the setting when it is not set.

#### Scenario: Upload an input for a job
- **WHEN** researcher `jdoe`, with Frontier remote folder `/r`, uploads `/mnt/data/uploads/salt.xyz` to destination `inputs`
- **THEN** the file is at `/r.jdoe.jobs/transfers/inputs/salt.xyz` on Frontier, and that path is reported

#### Scenario: Replace an earlier upload
- **WHEN** the same researcher uploads an edited `/mnt/data/uploads/salt.xyz` to `inputs` again
- **THEN** `/r.jdoe.jobs/transfers/inputs/salt.xyz` holds the edited file, and the call succeeds

#### Scenario: Destination outside transfers
- **WHEN** an agent uploads to destination `/lustre/orion/abc123/proj-shared` or `../src`
- **THEN** the call fails before contacting the cluster, saying uploads go only under the researcher's `transfers/` folder

#### Scenario: Local file outside the project
- **WHEN** an agent uploads `/etc/hosts` or `/mnt/data/output/../../etc/hosts`
- **THEN** the call fails before anything is uploaded

#### Scenario: Two files with one name
- **WHEN** an agent uploads `/mnt/data/uploads/a.txt` and `/mnt/data/output/a.txt` in one call
- **THEN** the call fails before anything is uploaded, saying two of the files have the same name

#### Scenario: Remote folder not set
- **WHEN** a researcher with no Odo remote folder uploads a file to Odo
- **THEN** the call fails before anything is uploaded, saying to set the Odo remote directory in the VISTA user settings

### Requirement: Hermetic transfer coverage
Listing, downloading and uploading SHALL be tested for Odo and Frontier against a faked Globus
client with no network access, including an expired session, a missing path, and each refused
argument above. Approval SHALL be tested with a faked researcher who approves, declines,
dismisses, or cannot be asked, including that a declined call moves nothing, and the download
cache with a remote file that changed in size, in modification time only, and not at all.

#### Scenario: PR CI
- **WHEN** PR CI runs
- **THEN** the transfer tests pass with no real credentials

#### Scenario: A decline is checked against the cluster
- **WHEN** the tests decline an upload
- **THEN** they assert the faked cluster received no upload and no new folder

### Requirement: The researcher approves each transfer
Each call to the upload or the download tool SHALL ask the researcher, in the chat, to approve
it before any file moves, and SHALL move nothing unless they approve. Listing SHALL NOT ask.
Every argument check SHALL run before the prompt, so a refused call asks nothing. An approval
SHALL cover only the call it was asked for; nothing SHALL be remembered for later calls.

The prompt SHALL name the cluster, each file that will move with its size, the total size, and
where the files will land. For an upload it SHALL give the absolute remote destination and name
each file there that the upload will replace. For a download it SHALL say which files will
replace a local copy because the remote file changed. It SHALL NOT list a file that will not
move: one already held locally and current, or one that is not on the cluster. When no file
would move, the tool SHALL NOT ask, and SHALL report as it would after an approval.

Nothing SHALL be written on the cluster, including any folder, before the researcher approves an
upload. The prompt SHALL offer two answers, Approve and Decline, and only Approve SHALL count as
approval. When the researcher declines or dismisses the prompt, the call SHALL fail, saying the
transfer was not approved, that the agent must not retry it, and that it should ask the
researcher what they want instead. A run that cannot show the researcher a prompt, or a prompt
that fails to be shown, SHALL get the same failure, which SHALL also say that transfers need the
researcher's approval in a chat.

#### Scenario: Approving an upload
- **WHEN** researcher `jdoe`, with Frontier remote folder `/r`, has an agent upload `/mnt/data/uploads/salt.xyz` (12 KB) to destination `inputs`, and approves the prompt
- **THEN** the prompt named Frontier, `salt.xyz` with 12 KB, and `/r.jdoe.jobs/transfers/inputs/`
- **AND** the file is uploaded and its remote path reported

#### Scenario: An upload that replaces a file
- **WHEN** `/r.jdoe.jobs/transfers/inputs/salt.xyz` is already on Frontier and an agent uploads `salt.xyz` to `inputs` again
- **THEN** the prompt says the upload replaces `salt.xyz`

#### Scenario: Declining an upload
- **WHEN** the researcher declines an upload to a destination folder that does not exist yet
- **THEN** the call fails saying the transfer was not approved and must not be retried
- **AND** nothing is uploaded and no folder is created on the cluster

#### Scenario: Approving a download
- **WHEN** an agent downloads two files from Odo of 1.5 GB and 0.5 GB, neither held locally, and the researcher approves
- **THEN** the prompt named Odo, both files with their sizes, a 2.0 GB total, and the local folder they land in
- **AND** both are downloaded

#### Scenario: Declining a download
- **WHEN** the researcher declines a download
- **THEN** the call fails saying the transfer was not approved and must not be retried, and no file is written locally

#### Scenario: Nothing to fetch
- **WHEN** an agent downloads a file whose local copy is current
- **THEN** no prompt is shown and the local copy's sandbox path is reported

#### Scenario: Missing files are not offered
- **WHEN** an agent downloads two files from Odo and one is not on the cluster
- **THEN** the prompt lists only the file that is there
- **AND** after approval the result reports the other as not on the cluster

#### Scenario: Refused arguments ask nothing
- **WHEN** an agent uploads to destination `../src`, or downloads `/lustre/../etc/passwd`
- **THEN** the call fails as for any refused argument, and no prompt is shown

#### Scenario: Listing asks nothing
- **WHEN** an agent lists a folder on Frontier
- **THEN** no prompt is shown

#### Scenario: No way to ask
- **WHEN** an agent in a one-shot API call with no chat calls the upload tool
- **THEN** the call fails saying transfers need the researcher's approval in a chat, and nothing is uploaded
