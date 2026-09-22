# hpc-job-contracts Specification

## ADDED Requirements

### Requirement: GPU catalog entries declare their environment dependency

A catalog entry that requires a GPU and a pre-provisioned Python environment SHALL
declare both in `cluster_defaults.json` and SHALL fail preflight — rather than
build, install, or silently continue on CPU — when the environment is unusable.

#### Scenario: Environment path is configuration, not code

- **WHEN** catalog contract tests load a GPU entry's `cluster_defaults.json`
- **THEN** the environment location SHALL be present in `iri.environment`
- **AND** relocating it SHALL require no change to a job script or wrapper

#### Scenario: Unusable environment is a preflight failure

- **WHEN** the declared environment is missing or its framework import fails
- **THEN** the job SHALL exit non-zero before invoking any science code
