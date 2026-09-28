## ADDED Requirements

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
- **AND** a route the fixtures do not cover SHALL fail the run rather than be silently substituted
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
- **THEN** it SHALL be annotated as an expected failure with a comment naming the cause
- **AND** it MUST NOT assert the defective behaviour as though it were correct
- **AND** the suite SHALL fail once the defect is fixed, so the annotation is removed
