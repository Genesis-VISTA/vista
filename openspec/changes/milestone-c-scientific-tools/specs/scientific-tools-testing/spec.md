## ADDED Requirements

### Requirement: Hermetic RAG search tests

`vista_mcp_server` SHALL ship a committed mini KB fixture (tiny Chroma DB or
mock embedder) so `rag_search` tests run in PR CI without downloading Hugging
Face models.

#### Scenario: Known query returns expected chunk

- **WHEN** `rag_search` runs against the mini fixture with a known query
- **THEN** the result SHALL include the expected chunk

#### Scenario: Unknown KB slug errors cleanly

- **WHEN** `rag_search` is called with a missing or unknown KB slug
- **THEN** the tool SHALL return a clean error (no uncaught exception / hang)

#### Scenario: Empty corpus behavior asserted

- **WHEN** the fixture corpus is empty
- **THEN** tests SHALL document and assert the empty-corpus behavior

### Requirement: Sandbox volume-mount boundary

`dev_mcp_server` tests SHALL prove the volume-mount security boundary that
exists today (`VISTA_DEV_MCP_VOLUMES` → sandbox spawn args). Tests MUST NOT
invent an application-level path jail; `create_file` / `view` / `run_bash` pass
paths to the guest and confinement is the mount into the microsandbox/container.

#### Scenario: Volume-mount wiring unit-tested hermetically

- **WHEN** PR CI runs `dev_mcp_server` volume-mount wiring tests
- **THEN** tests SHALL assert spawn args reflect configured volumes without a live daemon
- **AND** the test module SHALL document that the VM/mount is the security boundary

#### Scenario: Live escape attempts stay opt-in

- **WHEN** escape attempts are exercised against a real container
- **THEN** those tests MUST be marked `@pytest.mark.sandbox`
- **AND** they MUST NOT be required for merge while advisory

### Requirement: Cross-session volume isolation

Tenant / volume isolation coverage SHALL extend existing
`backend/tests/security/test_tenant_isolation.py` patterns for cross-session
isolation relevant to sandbox volumes where cheap.

#### Scenario: Sessions cannot access another session volume

- **WHEN** two sessions have distinct volumes
- **THEN** isolation tests SHALL fail closed if one session can read or write the other volume

### Requirement: Skills and project wiring tests

Tests SHALL assert SKILL.md load, skills block in the agent system prompt, that
unknown skill slugs are skipped with a warning (current production behavior),
and that project `tools` / `skills` / `knowledge_bases` change the tool list and
prompt (prefer Milestone B harness).

#### Scenario: Skill appears in system prompt

- **WHEN** a project lists a valid skill
- **THEN** the assembled agent system prompt SHALL include that skill's block

#### Scenario: Unknown skill slug warns and skips

- **WHEN** agent build or run setup references an unknown skill slug
- **THEN** the system SHALL warn and skip that skill (not invent a hard error)

### Requirement: Seed project snapshots

Seed projects `alloy-design` and `molten-salt` SHALL have snapshot assertions
for expected skill slugs and tool patterns against `seed.py` (seed data lives
inline; there is no `defaults.py`).

#### Scenario: Seed projects match expected wiring

- **WHEN** seed snapshot tests run
- **THEN** `alloy-design` and `molten-salt` SHALL match expected skills and tool patterns

### Requirement: Fernet crypto at rest for HPC tokens

Sensitive user HPC token fields SHALL round-trip through Fernet encryption:
raw DB bytes ≠ plaintext; ORM read returns plaintext. Tests SHALL set
`VISTA_BACKEND_ENCRYPTION_KEY` via fixture (ephemeral key allowed) and clear
`get_fernet`'s cache after patching settings.

#### Scenario: Encrypt decrypt round-trip

- **WHEN** a sensitive field is written and read through the ORM
- **THEN** the ORM value SHALL equal the plaintext
- **AND** the raw DB value SHALL NOT equal the plaintext

### Requirement: UI lib Vitest in CI

The UI package SHALL run Vitest unit tests for `ui/lib/*` (at least agent-events
parsing and chat-session helpers) via a required `ui:test` job in GitLab CI and
`scripts/ci-local.sh`. Playwright MUST NOT be added in this milestone.

#### Scenario: ui test is no longer a no-op

- **WHEN** `./scripts/ci-local.sh ui test` or the `ui:test` CI job runs
- **THEN** Vitest SHALL execute `ui/lib` unit tests
- **AND** failures SHALL fail the job
