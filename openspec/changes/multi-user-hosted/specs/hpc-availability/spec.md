## ADDED Requirements

### Requirement: Hosted supports only allowed clusters

A hosted deployment SHALL support only the clusters in the server's allowed-clusters setting,
which SHALL default to Odo alone. Status, settings fields, credential saves, Globus logins,
job submission and file transfer SHALL refuse any other cluster, in the backend and in the MCP
server, whatever the agent or a client asks. A researcher's hidden-clusters preference SHALL
stay a display choice and SHALL NOT be how this is enforced. A desktop install keeps every
supported cluster.

#### Scenario: Hosted cluster list

- **GIVEN** a hosted deployment with the default allowed clusters
- **WHEN** a researcher requests HPC status
- **THEN** the response contains Odo only

#### Scenario: A Frontier job asked for on hosted

- **GIVEN** a hosted deployment
- **WHEN** the agent calls the job submission tool for Frontier
- **THEN** the tool refuses with the reason that only Odo is available here, and nothing is submitted

### Requirement: Hosted checks a token's enclave on save

On a hosted deployment, saving an S3M token SHALL introspect it with S3M first and SHALL refuse
it unless its security enclave is the open enclave, with the reason. Saving the shared Globus
pair (the one used for every OLCF cluster) SHALL be refused; only a cluster's own pair is
accepted.

#### Scenario: A moderate-enclave token

- **GIVEN** a hosted deployment
- **WHEN** a researcher saves an S3M token whose introspection reports a moderate security enclave
- **THEN** the token is not stored, and the reason says only open-enclave tokens are accepted here

#### Scenario: S3M unreachable

- **WHEN** introspection cannot reach S3M
- **THEN** the token is not stored, and the reason says it could not be checked
