## MODIFIED Requirements

### Requirement: Uploading local files
The upload tool SHALL take a list of local files and a destination folder relative to the
researcher's `transfers/` folder on that cluster (see hpc-job-submission, One folder layout),
and SHALL upload each file there under its own name, creating any missing folders below
`transfers/`. It SHALL report each file's absolute remote path. It SHALL NOT write anywhere
else on the cluster: an absolute destination, or one containing `..` or a backslash, SHALL be
refused without contacting the cluster. A file of the same name already in the destination
SHALL be replaced. Two local files with the same name in one call SHALL be refused before
anything is uploaded, since one would replace the other.

Local files SHALL be given as sandbox paths under `/mnt/data/output`, `/mnt/data/uploads`, or
`/mnt/linked/<name>` for an available linked folder of the project (see linked-folders);
any other path, one that leaves those folders (including through a symlink whose real
location is outside its linked folder), a folder, or a missing file SHALL be refused
before anything is uploaded. Uploading SHALL need the researcher's remote folder for that
cluster, and SHALL fail naming the setting when it is not set.

#### Scenario: Upload an input for a job
- **WHEN** researcher `jdoe`, with Frontier remote folder `/r`, uploads `/mnt/data/uploads/salt.xyz` to destination `inputs`
- **THEN** the file is at `/r.jdoe.jobs/transfers/inputs/salt.xyz` on Frontier, and that path is reported

#### Scenario: Upload from a linked folder
- **WHEN** researcher `jdoe`, with Frontier remote folder `/r`, has a project linking a folder as `salt-data` that holds `big/traj.nc`, and the agent uploads `/mnt/linked/salt-data/big/traj.nc` to destination `inputs`
- **THEN** the file is at `/r.jdoe.jobs/transfers/inputs/traj.nc` on Frontier, read from the linked folder without a copy in the project volume

#### Scenario: Replace an earlier upload
- **WHEN** the same researcher uploads an edited `/mnt/data/uploads/salt.xyz` to `inputs` again
- **THEN** `/r.jdoe.jobs/transfers/inputs/salt.xyz` holds the edited file, and the call succeeds

#### Scenario: Destination outside transfers
- **WHEN** an agent uploads to destination `/lustre/orion/abc123/proj-shared` or `../src`
- **THEN** the call fails before contacting the cluster, saying uploads go only under the researcher's `transfers/` folder

#### Scenario: Local file outside the project
- **WHEN** an agent uploads `/etc/hosts`, `/mnt/data/output/../../etc/hosts`, `/mnt/linked/salt-data/../../etc/hosts`, or `/mnt/linked/salt-data/escape` where `escape` is a symlink to a file outside the linked folder
- **THEN** the call fails before anything is uploaded

#### Scenario: Unknown or unavailable link
- **WHEN** an agent uploads `/mnt/linked/nope/a.txt` and the project has no available linked folder named `nope`
- **THEN** the call fails before anything is uploaded

#### Scenario: Two files with one name
- **WHEN** an agent uploads `/mnt/data/uploads/a.txt` and `/mnt/data/output/a.txt` in one call
- **THEN** the call fails before anything is uploaded, saying two of the files have the same name

#### Scenario: Remote folder not set
- **WHEN** a researcher with no Odo remote folder uploads a file to Odo
- **THEN** the call fails before anything is uploaded, saying to set the Odo remote directory in the VISTA user settings
