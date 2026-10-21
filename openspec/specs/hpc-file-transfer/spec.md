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
descriptions SHALL say to leave bulk data on the cluster for a job to process there, and that
uploads land only in the researcher's `transfers/` folder.

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
file display can open it. Any file the researcher's identity can read SHALL be downloadable,
and the system SHALL impose no size limit. A file already saved there and not empty SHALL NOT
be fetched again. A path that is not on the cluster SHALL be reported as missing while the
other files are still downloaded. A relative path, one containing `..`, or `/` itself SHALL be
refused without contacting the cluster. An interrupted download SHALL NOT leave a partial file at the
reported path.

#### Scenario: Fetch a dataset file
- **WHEN** an agent downloads `/lustre/orion/abc123/proj-shared/data/a.nc` from Frontier
- **THEN** the file is saved at `/mnt/data/output/transfers/frontier/lustre/orion/abc123/proj-shared/data/a.nc`, and that path is reported

#### Scenario: Already downloaded
- **WHEN** the same file is downloaded a second time and the first copy is still there
- **THEN** it is not fetched from the cluster again, and the same sandbox path is reported

#### Scenario: One file missing
- **WHEN** an agent downloads two files from Odo and the first answers that it does not exist
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
argument above.

#### Scenario: PR CI
- **WHEN** PR CI runs
- **THEN** the transfer tests pass with no real credentials
