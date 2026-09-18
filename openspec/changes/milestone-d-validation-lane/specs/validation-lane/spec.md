## ADDED Requirements

### Requirement: Live and HPC env-flag gates

All live / real-HPC tests SHALL use `@pytest.mark.live` and/or `@pytest.mark.hpc`
and SHALL skip unless the corresponding env flag is set.

#### Scenario: Live tests require VISTA_RUN_LIVE

- **WHEN** `VISTA_RUN_LIVE` is unset
- **THEN** tests marked `live` SHALL skip
- **AND** when `VISTA_RUN_LIVE=1` they MAY run with configured model credentials

#### Scenario: HPC tests require VISTA_RUN_HPC

- **WHEN** `VISTA_RUN_HPC` is unset
- **THEN** tests marked `hpc` SHALL skip
- **AND** when `VISTA_RUN_HPC=1` they MAY submit real cluster smoke jobs

#### Scenario: PR CI stays hermetic

- **WHEN** PR CI runs pytest
- **THEN** the expression SHALL remain `not live and not hpc and not sandbox`
- **AND** live / HPC failures MUST NOT block merges

### Requirement: Nightly validation job skeleton

A GitLab scheduled (or documented manual) job SHALL run evaluation-runbook
agent-mode golden prompts with dry-run HPC and fault-recovery checks. Secrets
SHALL exist only in scheduled pipeline variables.

#### Scenario: Scheduled job does not affect MR pipelines

- **WHEN** an MR pipeline runs
- **THEN** it MUST NOT require AmSC inference keys or HPC tokens
- **AND** the nightly job SHALL use rules such as `$CI_PIPELINE_SOURCE == "schedule"` (or equivalent manual gate)

#### Scenario: Nightly exercises dry-run HPC and agent mode

- **WHEN** the scheduled validation job runs successfully configured
- **THEN** it SHALL run at least one agent-mode golden path and one dry-run HPC path
- **AND** failure ownership / flake policy SHALL be documented

### Requirement: Golden prompt suite

A checked-in golden prompt suite SHALL map prompts to expected tool-name
allowlists with soft asserts (preferred tools called; do not hard-fail on exact
final answer wording unless a separate rubric is added later).

#### Scenario: At least one case per default seed project

- **WHEN** both `molten-salt` and `alloy-design` remain seeded
- **THEN** the suite SHALL include at least one case per default project
- **AND** runs SHALL require `VISTA_RUN_LIVE=1` plus configured `VISTA_BACKEND_MODEL`

### Requirement: Optional weekly real HPC smoke

Real-cluster smoke for `hpc_jobs/example` SHALL be marked `@pytest.mark.hpc`,
enabled only with `VISTA_RUN_HPC=1`, and SHOULD use a weekly or manual schedule
rather than default nightly if flaky.

#### Scenario: Real HPC smoke documents terminal outcomes

- **WHEN** real HPC smoke runs with valid credentials
- **THEN** documentation/tests SHALL record expected job id, terminal status, and output fetchability (or cluster-specific limits)

### Requirement: Seeded Playwright smoke outside PR CI

The **seeded** Playwright flow (open app → select project → send message →
observe tool card and/or elicitation modal), which needs a standing stack with
a real model and a seeded project, SHALL run only on schedule or manual
invocation, not in MR CI.

This scopes the requirement to the flow that needs credentials and live
infrastructure. A hermetic browser suite, one that starts only the Next server
and answers every backend route from fixtures, is a different thing: it needs
nothing to run, so keeping it out of MR CI would buy nothing. That suite is
required on every MR.

#### Scenario: Seeded smoke not required to merge

- **WHEN** an MR pipeline runs
- **THEN** the seeded Playwright smoke MUST NOT be a required job
- **AND** selectors SHOULD prefer role/text over brittle CSS

#### Scenario: A hermetic browser suite may be required

- **WHEN** a browser suite intercepts every backend route and needs no
  credentials, no seeded data and no live services
- **THEN** it MAY be a required MR job
- **AND** a request that escapes to a real origin SHALL fail the run

### Requirement: Evaluation runbook remains operational source

Milestone D SHALL own the automation wrapper and pass/fail summary while
`docs/evaluation-runbook.md` remains the step-by-step for metrics JSONL,
amortization, and concurrency curves.

#### Scenario: Cross-links between runbook and validation lane

- **WHEN** operators follow automated validation docs
- **THEN** they SHALL find cross-links between the validation-lane change/spec and the evaluation runbook
