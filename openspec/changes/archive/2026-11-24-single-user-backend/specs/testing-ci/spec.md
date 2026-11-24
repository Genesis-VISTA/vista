## MODIFIED Requirements

### Requirement: VISTAGuard out of scope

VISTAGuard gates (G1–G7), Q-LLM, approval-capability security suites, and
red-team harnesses SHALL remain out of scope for the testing roadmap changes
under `openspec/changes/milestone-*`.

#### Scenario: No guard deliverables in testing milestones

- **WHEN** a testing-roadmap change is proposed or applied
- **THEN** it MUST NOT add VISTAGuard gate tests or red-team harnesses
- **AND** existing volume isolation tests MAY remain as isolation coverage between projects
