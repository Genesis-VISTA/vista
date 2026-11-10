# Spec Delta

## MODIFIED Requirements

### Requirement: The hello job is a round trip

Each hello job SHALL read a small input file shipped with the job, and SHALL write under its
output folder a report that gives the host and node list, the GPUs it sees, the loaded
modules, the run's start and end time, and the input's contents with a checksum. The skill
SHALL tell the agent to submit the job, follow its log, fetch the report once the job
finishes, and show the researcher what it found. The job SHALL need no setting beyond what
the cluster already requires to submit: the cluster's S3M token, its Globus connection
(shared by Frontier and Lux) and its remote directory.

#### Scenario: A hello run in dry-run mode

- **WHEN** the agent runs `hello-frontier` against the dry-run HPC backend
- **THEN** it submits the job, reads its log, fetches the report and shows it, and the
  submitted job carries the input file

#### Scenario: A live hello run

- **WHEN** a researcher connected to Frontier asks to run hello-world there
- **THEN** the job runs on a Frontier compute node and the researcher sees its report,
  including the input's checksum as it was uploaded

#### Scenario: A live hello run on Lux

- **WHEN** a researcher with a Lux S3M token and Frontier's Globus connection asks to run hello-world on Lux
- **THEN** the job runs on a Lux compute node, without any login prompt, and the researcher
  sees its report, including the GPU it was given and the input's checksum as it was uploaded

### Requirement: A cluster that is not ready is named, not retried

When a hello job cannot be submitted because a connection or setting its cluster needs is
missing, no job SHALL be submitted, and the skill SHALL tell the agent to tell the researcher
which connection is missing and that it is made in Settings, and not to retry the submission
until the researcher says it is done.

#### Scenario: Lux without a token

- **WHEN** a researcher asks to run `hello-lux` and no Lux S3M token is set
- **THEN** no job is submitted, and the agent says that a Lux S3M token must be added in
  Settings

#### Scenario: Lux without an account

- **WHEN** a researcher with a Lux S3M token, Frontier's Globus connection and a Lux remote
  directory, but no Lux account, asks to run `hello-lux`
- **THEN** the job is submitted, charged to the token's project, and no Lux account is asked for

#### Scenario: Frontier without Globus

- **WHEN** a researcher asks to run `hello-frontier` and Globus is not connected for Frontier
- **THEN** no job is submitted, and the agent says that Globus must be connected for Frontier
