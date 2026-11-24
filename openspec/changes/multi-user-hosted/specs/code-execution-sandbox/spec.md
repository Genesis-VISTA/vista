## ADDED Requirements

### Requirement: Sandboxes are per user and capped

Each sandbox and its volume SHALL belong to one user, and no user's sandbox SHALL see another
user's files. The server SHALL cap sandboxes per user (default 4) and in all (default 40),
both server settings. At a cap the user's least recently used idle sandbox SHALL be closed
first; when none is idle the request SHALL be refused with the reason. Nothing SHALL be closed
while a command it started is running.

#### Scenario: A fifth conversation

- **GIVEN** Alice has 4 sandboxes, one of them idle
- **WHEN** she starts a fifth conversation that needs a sandbox
- **THEN** her idle sandbox is closed and the new one starts, and nobody else's sandbox is touched

### Requirement: A sandbox receives no server secrets

A sandbox's command environment SHALL contain only what the sandbox needs, never the
backend's own environment: no encryption key, model or Slack keys, or MCP shared secret.

#### Scenario: Reading the environment

- **WHEN** the agent runs `env` in its sandbox
- **THEN** the output contains no `VISTA_BACKEND_*` value and no server key
