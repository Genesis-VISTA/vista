# Spec Delta

## MODIFIED Requirements

### Requirement: Fake IRI and Globus clients

Unit tests SHALL exercise JobSpec construction for Odo, Perlmutter, Frontier and Lux
using `FakeIriClient` / `FakeGlobusClient` without network access. The IRI
client SHALL be tested against a faked HTTP transport for submit, status and cancel,
including the request path for each cluster's resource id, a 404 for an unknown
resource, a 401, and a status payload whose `message` is the string `None`; and, for
Perlmutter, for each filesystem operation as a task that completes, fails, or does not
finish in time. The MCP server's tests SHALL run with no private package installed.

#### Scenario: JobSpec happy paths with network disabled

- **WHEN** submit-path unit tests run with network disabled
- **THEN** Odo, Perlmutter, Frontier and Lux JobSpec assertions SHALL pass
- **AND** they MUST NOT require real IRI or Globus credentials

#### Scenario: Requests name the cluster's resource

- **WHEN** the IRI client submits, polls and cancels a job for each of Odo, Frontier, Lux and Perlmutter
- **THEN** the faked transport sees `POST .../api/<v>/compute/job/<id>`, `GET .../api/<v>/compute/status/<id>/<job>` and `DELETE .../api/<v>/compute/cancel/<id>/<job>` on that cluster's facility, with `<v>` the cluster's configured version (`v2` for OLCF, `v1` for Perlmutter) and the cluster's own bearer token

#### Scenario: Filesystem tasks

- **WHEN** the IRI client lists a Perlmutter folder, at v1 and at v2
- **THEN** the faked transport sees `GET .../api/v1/filesystem/ls/<storage id>?path=...` (v1) or `POST .../api/v2/filesystem/ls/<storage id>` with the path in the JSON body (v2), then `GET .../api/<v>/task/<task id>` until the task completes, and the listing comes from the task's result

### Requirement: Dry-run HPC lifecycle coverage

Dry-run tests SHALL cover submit lifecycle paths for supported clusters,
including cancel-all-clusters behavior where applicable.

#### Scenario: Dry-run covers perlmutter and frontier

- **WHEN** dry-run tests execute
- **THEN** perlmutter, frontier and lux paths SHALL be covered in addition to baseline clusters
- **AND** cancel-all-clusters behavior SHALL be asserted where implemented

### Requirement: IRI status golden fixtures

Golden fixtures for IRI status payloads SHALL lock parsing / formatting for
completed, failed, running, queued and cancelled states (including Perlmutter status
formatting and OLCF v2 payloads recorded from Lux, with identifiers and tokens removed).

#### Scenario: Completed and running fixtures parse

- **WHEN** status formatting tests load golden fixtures for completed and running jobs
- **THEN** parsed / formatted output SHALL match the expected golden results

#### Scenario: v2 failed and queued fixtures parse

- **WHEN** status formatting tests load the v2 fixtures for a job that exited 7 and a queued job
- **THEN** the first reports FAILED with exit code 7, and the second reports QUEUED with no exit code and no message
