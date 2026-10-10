# Spec Delta

## MODIFIED Requirements

### Requirement: Job catalog on-disk contracts

Every entry under `hpc_jobs/`, and every job under `hpc_jobs/` inside a skill
that VISTA ships (`backend/src/vista_backend/db/skills/<skill>/hpc_jobs/`),
SHALL satisfy catalog contract tests (parametrized on-disk checks,
`get_available_jobs()` parity, and negative cases). The same checks SHALL run
against the job lookup used for skill jobs, using a skill fixture.

#### Scenario: Broken catalog entry fails CI

- **WHEN** a broken entry is added under `hpc_jobs/` (missing README, no script, or bad header)
- **THEN** catalog contract tests SHALL fail

#### Scenario: Broken skill job fails CI

- **WHEN** a broken job is added under a shipped skill's `hpc_jobs/`
- **THEN** catalog contract tests SHALL fail

#### Scenario: Disk and API catalog agree

- **WHEN** catalog tests run against a valid tree
- **THEN** `get_available_jobs()` results SHALL match the on-disk job set

#### Scenario: Skill jobs are found and named

- **WHEN** the resolver tests run against a skills folder fixture holding a valid job and a broken one
- **THEN** the valid job resolves as `<skill>/<job>` to the skill's folder, and the broken one is refused with the rule it breaks
