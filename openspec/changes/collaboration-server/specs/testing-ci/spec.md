## MODIFIED Requirements

### Requirement: Local CI mirror

`./scripts/ci-local.sh` SHALL mirror GitLab CI targets (`backend`, `collab`, `ui`, `mcp`,
`electron`) and actions (`lint`, `test`) so developers can reproduce pipeline failures
locally.

#### Scenario: MCP target covers both servers

- **WHEN** a developer runs `./scripts/ci-local.sh mcp test`
- **THEN** both `dev_mcp_server` and `vista_mcp_server` test suites run
- **AND** required `vista_mcp` failures SHALL fail the script

#### Scenario: UI target runs both hermetic suites

- **WHEN** a developer runs `./scripts/ci-local.sh ui test`
- **THEN** the component suite and the hermetic browser suite both run
- **AND** with `--fast`, only the component suite runs

#### Scenario: Collab target covers the collaboration server

- **WHEN** a developer runs `./scripts/ci-local.sh collab`
- **THEN** the collaboration server is linted, type-checked and tested, as `collab:lint`,
  `collab:typecheck` and `collab:test` do in GitLab CI
- **AND** `./scripts/ci-local.sh backend test` also runs the backend's collaboration tests
  against the server mounted in-process
