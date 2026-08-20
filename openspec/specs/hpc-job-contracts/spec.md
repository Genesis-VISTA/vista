# hpc-job-contracts Specification

## Purpose

Validate the curated `hpc_jobs/` catalog and the HPC submit path without talking
to a cluster — dry-run, fakes, and golden fixtures wired into required CI.

**Status:** Implemented (testing roadmap Milestone A).

## Requirements

### Requirement: Required vista-mcp CI jobs

GitLab CI and `./scripts/ci-local.sh` SHALL run `vista_mcp_server` lint and test
jobs; the test job SHALL be required (not `allow_failure`).

#### Scenario: Broken vista-mcp tests fail local mcp test

- **WHEN** `./scripts/ci-local.sh mcp test` runs and `vista_mcp_server` tests fail
- **THEN** the script SHALL exit non-zero

#### Scenario: GitLab requires vista-mcp:test

- **WHEN** an MR pipeline runs
- **THEN** job `vista-mcp:test` SHALL be present and required to merge
- **AND** it SHALL use hermetic marker filter `not live and not hpc and not sandbox`

### Requirement: Job catalog on-disk contracts

Every entry under `hpc_jobs/` SHALL satisfy catalog contract tests
(parametrized on-disk checks, `get_available_jobs()` parity, and negative cases).

#### Scenario: Broken catalog entry fails CI

- **WHEN** a broken entry is added under `hpc_jobs/` (missing README, no script, or bad header)
- **THEN** catalog contract tests SHALL fail

#### Scenario: Disk and API catalog agree

- **WHEN** catalog tests run against a valid tree
- **THEN** `get_available_jobs()` results SHALL match the on-disk job set

### Requirement: Fake IRI and Globus clients

Unit tests SHALL exercise JobSpec construction for Odo, Perlmutter, and Frontier
using `FakeIriClient` / `FakeGlobusClient` without network access.

#### Scenario: JobSpec happy paths with network disabled

- **WHEN** submit-path unit tests run with network disabled
- **THEN** Odo, Perlmutter, and Frontier JobSpec assertions SHALL pass
- **AND** they MUST NOT require real IRI or Globus credentials

### Requirement: Dry-run HPC lifecycle coverage

Dry-run tests SHALL cover submit lifecycle paths for supported clusters,
including cancel-all-clusters behavior where applicable.

#### Scenario: Dry-run covers perlmutter and frontier

- **WHEN** dry-run tests execute
- **THEN** perlmutter and frontier paths SHALL be covered in addition to baseline clusters
- **AND** cancel-all-clusters behavior SHALL be asserted where implemented

### Requirement: IRI status golden fixtures

Golden fixtures for IRI status payloads SHALL lock parsing / formatting for
completed and running states (including Perlmutter status formatting).

#### Scenario: Completed and running fixtures parse

- **WHEN** status formatting tests load golden fixtures for completed and running jobs
- **THEN** parsed / formatted output SHALL match the expected golden results
