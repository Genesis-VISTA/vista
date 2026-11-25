## ADDED Requirements

### Requirement: Sandboxes are per user and capped

Each sandbox and its volume SHALL belong to one user, and no user's sandbox SHALL see another
user's files. The server SHALL cap sandboxes per user (default 4 when hosted; on a desktop,
whose one user owns the machine, the total) and in all (default 40), both server settings.
At the user's cap their least recently used idle sandbox SHALL be closed; at the server's
cap, the least recently used idle sandbox of anyone. When none is idle the turn SHALL be
refused with the reason. Nothing SHALL be closed while a command it started is running.

#### Scenario: A fifth conversation

- **GIVEN** Alice has 4 sandboxes, one of them idle
- **WHEN** she starts a fifth conversation that needs a sandbox
- **THEN** her idle sandbox is closed and the new one starts, and nobody else's sandbox is touched

#### Scenario: The server is full

- **GIVEN** the server has 40 sandboxes, the oldest idle one Bob's
- **WHEN** Carol, who has none, starts a conversation that needs one
- **THEN** Bob's idle sandbox is closed and Carol's starts; Bob's next turn there starts a new
  one, with his files as they were

#### Scenario: Nothing idle

- **GIVEN** every sandbox on the server is running a turn or a command
- **WHEN** Carol sends a message
- **THEN** her turn fails with the reason ("This server has 40 sandboxes open, all busy…"),
  before the model is asked

### Requirement: A sandbox receives no server secrets

A sandbox's command environment SHALL contain only what the sandbox needs, never the
backend's own environment: no encryption key, model or Slack keys, or MCP shared secret.

#### Scenario: Reading the environment

- **WHEN** the agent runs `env` in its sandbox
- **THEN** the output contains no `VISTA_BACKEND_*` value and no server key
