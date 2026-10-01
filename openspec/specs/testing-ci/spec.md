# testing-ci Specification

## Purpose

Define how VISTA keeps pull-request CI hermetic, marker-gated, and locally
reproducible — shared rules for backend, MCP servers, and UI test lanes.

## Requirements

### Requirement: Hermetic PR CI

PR and default local CI SHALL run without AmSC API keys, Globus credentials,
or real Slurm / cluster access.

#### Scenario: Default merge pipeline needs no live secrets

- **WHEN** a developer runs `./scripts/ci-local.sh test` or a GitLab MR pipeline
- **THEN** all required jobs complete without live LLM, Globus, or HPC credentials
- **AND** failures that require those secrets MUST NOT block merges

### Requirement: Pytest marker registry

Backend, `vista_mcp_server`, and `dev_mcp_server` SHALL register shared pytest
markers: `unit`, `integration`, `live`, `sandbox`, and `hpc`.

#### Scenario: Hermetic default expression

- **WHEN** PR CI or the default local test invocation runs pytest
- **THEN** the marker expression SHALL be `not live and not hpc and not sandbox`
- **AND** tests marked `live`, `hpc`, or `sandbox` SHALL be excluded from required PR jobs

#### Scenario: Sandbox advisory until stabilized

- **WHEN** sandbox-marked tests run in CI before the sandbox lane is stable
- **THEN** those jobs MAY be `allow_failure` until the owning milestone stabilizes them
- **AND** they MUST NOT be required to merge while advisory

### Requirement: Fake at boundaries

Tests SHALL mock external IRI, Globus, and LLM clients at system boundaries
while keeping catalog parsing, metadata injection, and HPC dry-run paths real.

#### Scenario: Network-disabled submit-path unit tests

- **WHEN** HPC submit-path unit tests run with network disabled
- **THEN** they SHALL pass using fakes / dry-run
- **AND** they MUST NOT call real IRI or Globus endpoints

### Requirement: Local CI mirror

`./scripts/ci-local.sh` SHALL mirror GitLab CI targets (`backend`, `ui`, `mcp`)
and actions (`lint`, `test`) so developers can reproduce pipeline failures locally.

#### Scenario: MCP target covers both servers

- **WHEN** a developer runs `./scripts/ci-local.sh mcp test`
- **THEN** both `dev_mcp_server` and `vista_mcp_server` test suites run
- **AND** required `vista_mcp` failures SHALL fail the script

### Requirement: VISTAGuard out of scope

VISTAGuard gates (G1–G7), Q-LLM, approval-capability security suites, and
red-team harnesses SHALL remain out of scope for the testing roadmap changes
under `openspec/changes/milestone-*`.

#### Scenario: No guard deliverables in testing milestones

- **WHEN** a testing-roadmap change is proposed or applied
- **THEN** it MUST NOT add VISTAGuard gate tests or red-team harnesses
- **AND** existing tenant / volume isolation tests MAY remain as multi-tenancy coverage

### Requirement: Milestone delivery conventions

Testing milestones SHALL ship as focused MRs with conventional commits
(`test:`, `ci:`, `docs:`) and acceptance criteria tied to the owning OpenSpec
change.

#### Scenario: One milestone per MR

- **WHEN** implementing an open testing change (e.g. Milestone B)
- **THEN** the MR SHOULD cover that change only
- **AND** the MR description SHALL reference the OpenSpec change and its acceptance scenarios

### Requirement: Hermetic UI test lane

The UI SHALL have required MR jobs that execute UI code, not only lint and
type-check it. Those jobs SHALL run without a Python backend, MCP server, model
provider, or seeded database.

#### Scenario: Component tests run on every MR

- **WHEN** an MR pipeline runs
- **THEN** a required `ui:test` job SHALL execute the UI component test suite
- **AND** it MUST NOT set `allow_failure`
- **AND** it SHALL require no credentials of any kind

#### Scenario: Approval and elicitation dialogs are covered

- **WHEN** the UI component suite runs
- **THEN** it SHALL assert the rendering and confirm/deny behavior of the tool-approval and elicitation dialogs

#### Scenario: Browser flow answers every network call from a fixture

- **WHEN** the required hermetic browser job runs
- **THEN** every backend route the flow touches SHALL be answered from a fixture inside the page, including a streaming response the test drives event by event
- **AND** a request that escapes those fixtures SHALL fail the run rather than reach a real backend, MCP server, or model provider
- **AND** a request to a route the fixtures do not cover SHALL be answered with an error status and recorded, and a spec that checks the record SHALL fail on it rather than pass with a silent substitute
- **AND** the job SHALL block the merge on failure

#### Scenario: Seeded smoke stays outside MR CI

- **WHEN** the scheduled validation lane runs its seeded browser smoke
- **THEN** that job SHALL remain schedule-or-manual and `allow_failure`
- **AND** it SHALL remain separate from the hermetic MR job

### Requirement: Tests assert or fail

A UI test that cannot reach the state it is meant to check SHALL fail rather
than skip itself and report success.

#### Scenario: Missing fixture is a failure

- **WHEN** a UI test depends on a fixture or seeded entity that is absent
- **THEN** the test SHALL fail with a message naming what was missing
- **AND** it MUST NOT pass by skipping its assertions

### Requirement: Known defects are pinned, not encoded as correct

A test that reproduces a known defect SHALL be marked as expected to fail, so
the suite stays green while the defect stands and turns red once it is fixed.

#### Scenario: A reproduced defect keeps the suite honest

- **WHEN** a test reproduces a defect that this change does not fix
- **THEN** it SHALL be annotated as an expected failure (`it.fails` in Vitest, `test.fail` in Playwright) with a comment naming the cause
- **AND** it MUST NOT assert the defective behaviour as though it were correct
- **AND** the suite SHALL fail once the defect is fixed, so the annotation is removed
