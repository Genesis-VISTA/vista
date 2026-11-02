## Purpose

Defines the bundled hello-world skills, one per OLCF cluster VISTA supports, that take a
researcher from a connected cluster to a finished first job and its fetched output, and that
name the cause plainly when a cluster is not ready.

## ADDED Requirements

### Requirement: A hello skill for each OLCF cluster

VISTA SHALL bundle the skills `hello-odo`, `hello-frontier` and `hello-lux`. Each SHALL ship
exactly one HPC job, for its own cluster only, and SHALL pass the HPC skill validator. Each
job SHALL follow the `hpc-skill-author` job template for its cluster.

#### Scenario: The skill library on a fresh install

- **WHEN** VISTA starts for the first time
- **THEN** `hello-odo`, `hello-frontier` and `hello-lux` are in the skill library

#### Scenario: Each skill is valid

- **WHEN** the HPC skill validator is run on each hello skill
- **THEN** it passes

### Requirement: The hello job is a round trip

Each hello job SHALL read a small input file shipped with the job, and SHALL write under its
output folder a report that gives the host and node list, the GPUs it sees, the loaded
modules, the run's start and end time, and the input's contents with a checksum. The skill
SHALL tell the agent to submit the job, follow its log, fetch the report once the job
finishes, and show the researcher what it found. The job SHALL need no setting beyond what
the cluster already requires to submit: the S3M connection for Odo and Frontier, and the Lux
account setting for Lux.

#### Scenario: A hello run in dry-run mode

- **WHEN** the agent runs `hello-frontier` against the dry-run HPC backend
- **THEN** it submits the job, reads its log, fetches the report and shows it, and the
  submitted job carries the input file

#### Scenario: A live hello run

- **WHEN** a researcher connected to Frontier asks to run hello-world there
- **THEN** the job runs on a Frontier compute node and the researcher sees its report,
  including the input's checksum as it was uploaded

### Requirement: A cluster that is not ready is named, not retried

When a hello job cannot be submitted because a connection or setting its cluster needs is
missing, no job SHALL be submitted, and the skill SHALL tell the agent to tell the researcher
which connection is missing and that it is made in Settings, and not to retry the submission
until the researcher says it is done.

#### Scenario: Lux without an account

- **WHEN** a researcher asks to run `hello-lux` and no Lux account is set
- **THEN** no job is submitted, and the agent says that the Lux account must be set in
  Settings

#### Scenario: Frontier without Globus

- **WHEN** a researcher asks to run `hello-frontier` and Globus is not connected for Frontier
- **THEN** no job is submitted, and the agent says that Globus must be connected for Frontier

### Requirement: An expired file session is not an empty result

When fetching a hello job's report fails because the cluster's file session has expired, the
agent SHALL tell the researcher to reconnect, and SHALL NOT report the job as having produced
no output.

#### Scenario: Globus session expired after a long queue wait

- **WHEN** a `hello-frontier` job finishes after the Globus session for Frontier has expired
- **THEN** the agent says the session expired and that reconnecting Globus in Settings will
  let it fetch the report, and does not say the output folder is empty
