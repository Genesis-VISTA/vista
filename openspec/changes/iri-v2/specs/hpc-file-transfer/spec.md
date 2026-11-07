# Spec Delta

## MODIFIED Requirements

### Requirement: Transfer tools
The system SHALL offer agents three tools for Odo, Frontier and Lux: one that lists a remote
path, one that downloads remote files, and one that uploads local files. Each SHALL take the
cluster as a required argument whose only accepted values are `odo`, `frontier` and `lux`.
Each SHALL be authorized only by the researcher's Globus credential for that cluster (see
hpc-job-submission, Researcher's own Globus credential; Lux uses Frontier's connection and
collection), and SHALL need no S3M token. The
system SHALL hand each transfer tool call the researcher's settings and credentials, as it
does for the job tools, and SHALL NOT hand them to tools that are neither. The tool
descriptions SHALL say to leave bulk data on the cluster for a job to process there, that
uploads land only in the researcher's `transfers/` folder, that the researcher approves
each upload and download, and that a download fetches a file again by itself when it changed on
the cluster, so a local copy need never be deleted to refresh it.

#### Scenario: Another cluster is refused
- **WHEN** an agent calls any transfer tool with `cluster="perlmutter"`
- **THEN** the call is rejected for an invalid argument, and no cluster is contacted

#### Scenario: Lux uses Frontier's connection
- **WHEN** a researcher who connected Globus from Frontier's settings asks an agent to list a folder on Lux
- **THEN** the call lists it through the Frontier collection with that connection

#### Scenario: Lux uploads use the Lux folder
- **WHEN** a researcher with Lux remote folder `/r` uploads `salt.xyz` to Lux destination `inputs`
- **THEN** it is at `/r.<user>.jobs/transfers/inputs/salt.xyz`, where `<user>` is the owner Globus reports for their home folder

#### Scenario: Globus not connected
- **WHEN** a researcher with no Globus connection for Frontier, and no shared one, calls a transfer tool for Frontier
- **THEN** the call fails, saying to connect Globus for Frontier in the VISTA user settings

#### Scenario: A connected researcher's call carries the connection
- **WHEN** a researcher who has connected Globus for Odo asks an agent to list an Odo folder
- **THEN** the call uses their Odo Globus credential, and does not fail saying Globus is not connected

#### Scenario: Present in every project
- **WHEN** an agent's tools are listed in a project with no skills attached
- **THEN** the three transfer tools are among them

### Requirement: Hermetic transfer coverage
Listing, downloading and uploading SHALL be tested for Odo, Frontier and Lux against a faked
Globus client with no network access, including an expired session, a missing path, and each
refused argument above. Approval SHALL be tested with a faked researcher who approves, declines,
dismisses, or cannot be asked, including that a declined call moves nothing, and the download
cache with a remote file that changed in size, in modification time only, and not at all.

#### Scenario: PR CI
- **WHEN** PR CI runs
- **THEN** the transfer tests pass with no real credentials

#### Scenario: A decline is checked against the cluster
- **WHEN** the tests decline an upload
- **THEN** they assert the faked cluster received no upload and no new folder
