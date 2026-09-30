# hpc-job-contracts Specification

## ADDED Requirements

### Requirement: Frontier's OLCF project comes from the caller's token

The Slurm account for a Frontier job SHALL be the `project` claim of the caller's
S3M token, unless the job pins one in `cluster_defaults.json`. VISTA SHALL NOT
require that project to equal a deployment-wide constant, and SHALL NOT ask the
user to name it.

#### Scenario: A researcher outside the deployment project can submit

- **WHEN** a user whose S3M token names project `abc123` submits a Frontier job
  that pins no account
- **THEN** the JobSpec's account SHALL be `abc123`
- **AND** the submission SHALL NOT be refused for not being the deployment project

#### Scenario: A job-pinned account still wins

- **WHEN** a job's `cluster_defaults.json` names an `account`
- **THEN** that account SHALL be used
- **AND** a token naming a different project SHALL be refused, naming both projects

#### Scenario: Existing users are unaffected

- **WHEN** a user whose token names the deployment's own project submits
- **THEN** the account, remote directory, and Globus identity SHALL be exactly
  those used before per-user accounts existed

### Requirement: Job directories follow the account

A Frontier job's remote directory SHALL be resolved as the job's own
`remote_dir`, else the configured deployment directory when the account is the
deployment's own project, else the configured per-project template.

#### Scenario: Another project's files land in that project's space

- **WHEN** the effective account is not the deployment's project and the job pins
  no `remote_dir`
- **THEN** the base directory SHALL be the template rendered with that account
- **AND** it MUST NOT be the deployment project's directory

### Requirement: The deployment Globus identity serves only its own project

When the effective account is not the deployment's own project, VISTA SHALL use
only Globus credentials the caller connected, and SHALL NOT fall back to the
deployment's credentials.

#### Scenario: An outside user without their own Globus is refused

- **WHEN** a user whose token names another project has connected no Globus
  credential and triggers a Frontier file operation
- **THEN** the operation SHALL be refused
- **AND** the message SHALL say that their own Globus connection is required
  because the job runs under their project

#### Scenario: An outside user with their own Globus proceeds

- **WHEN** such a user has connected a complete Globus credential pair
- **THEN** their own credential SHALL be used

### Requirement: A project's first run reports missing directory permissions

Before submitting, VISTA SHALL verify the job's base directory exists and is
group-writable, and SHALL name the exact command to fix it when not.

#### Scenario: An unprepared project gets an instruction, not a dead job

- **WHEN** the resolved base directory is absent or not group-writable
- **THEN** submission SHALL be refused before the job is created
- **AND** the message SHALL contain `mkdir -p -m 2775` (or `chmod 2775`) with the
  resolved path
- **AND** it SHALL explain that the IRI automation user cannot otherwise write
  there

### Requirement: The effective account is recorded with the job

The account actually used SHALL be stored with the submitted job, so later status
and output calls re-verify the caller against the same project.

#### Scenario: Status after a restart checks the project the job ran under

- **WHEN** a job submitted under a non-default account is polled after the job
  registry is reloaded from disk
- **THEN** the access check SHALL verify the caller's token against that job's
  recorded account
