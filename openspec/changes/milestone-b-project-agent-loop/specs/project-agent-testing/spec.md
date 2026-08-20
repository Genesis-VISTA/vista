## ADDED Requirements

### Requirement: Shared ProjectAgent test harness

The backend test suite SHALL provide an in-process harness that constructs
`ProjectAgent` (or a thin agent under test) with `FunctionModel` and a fake MCP
toolset, without launching real STDIO `dev_mcp_server` or HTTP `vista_mcp_server`
in unit tests.

#### Scenario: Fake toolset stubs core tools

- **WHEN** unit tests build the harness
- **THEN** the fake MCP toolset SHALL expose stubs for `rag_search`, `submit_hpc_job`, and `display_file`
- **AND** `submit_hpc_job` SHALL return dry-run-like summary strings
- **AND** an optional `run_bash` stub MAY be included

#### Scenario: Minimal project and user fixtures

- **WHEN** a test needs an agent under test
- **THEN** helpers SHALL create a minimal `Project` and `User` in the in-memory DB session
- **AND** documentation SHALL describe how to opt into real MCP servers for later integration tests

### Requirement: System prompt assembly tests

Unit tests SHALL assert `ProjectAgent` system prompt composition from base
prompt, project prompt, knowledge-base slugs, and skills content.

#### Scenario: Prompt includes project and skills

- **WHEN** a project has a custom `system_prompt`, configured KB slugs, and skills
- **THEN** the assembled system prompt SHALL include base text from `base_system_prompt.md`
- **AND** it SHALL append the project `system_prompt`
- **AND** it SHALL list configured KB slugs
- **AND** it SHALL include the skills XML / block from the skill loader

### Requirement: Tool allow and deny filtering

Unit tests SHALL table-drive `_tool_allowed` fnmatch allow / deny patterns.

#### Scenario: Deny patterns block tools

- **WHEN** a tool name matches a configured deny pattern
- **THEN** `_tool_allowed` SHALL return false
- **AND** allow-pattern cases SHALL pass for matching permitted tools

### Requirement: Vista metadata shape

Unit tests SHALL assert `_build_vista_metadata` includes user token fields and
volume / `project_paths` shape consistent with MCP invoke metadata patterns.

#### Scenario: Metadata includes project paths

- **WHEN** vista metadata is built for a project agent run
- **THEN** the metadata SHALL include user token fields and `project_paths` / volume shape expected by MCP tools

### Requirement: One-turn component coverage

Component tests SHALL drive a scripted one-turn agent loop with `FunctionModel`.

#### Scenario: Allowed tool then final answer

- **WHEN** the scripted model calls an allowed tool and then produces final text
- **THEN** the tool SHALL have been invoked
- **AND** `ProjectAgentResult` SHALL indicate success

#### Scenario: Usage limits stop runaway tool loops

- **WHEN** the scripted model keeps requesting tools beyond project `usage_limits.request_limit`
- **THEN** the turn SHALL stop at the configured limit

#### Scenario: Elicitation and approval event plumbing

- **WHEN** callbacks already wired on `ProjectAgent` emit elicitation or tool-approval events
- **THEN** stream event types SHALL include `McpElicitationEvent` and/or `McpToolApprovalEvent`
- **AND** tests MUST NOT enable or assert VISTAGuard gates

### Requirement: Agent HTTP and SSE contract

API tests SHALL cover `POST /projects/{name}/agent/run` streaming and authz
using `httpx.AsyncClient` with dependency overrides (fake agent pool / scripted `run_stream`).

#### Scenario: Scripted turn emits expected SSE kinds

- **WHEN** a member runs the agent endpoint with a scripted turn
- **THEN** SSE event kinds SHALL match the scripted turn contract

#### Scenario: Non-member receives 403

- **WHEN** a non-member of the project calls `POST /projects/{name}/agent/run`
- **THEN** the response status SHALL be 403

#### Scenario: Missing project receives 404

- **WHEN** the project name does not exist
- **THEN** the response status SHALL be 404

### Requirement: Chat session history on completed turns

Completed agent turns with a `chat_session_id` SHALL append message history;
stateless turns MUST NOT invent a durable session.

#### Scenario: Session id appends history

- **WHEN** a turn completes with a valid `chat_session_id`
- **THEN** message history for that session SHALL include the turn

#### Scenario: Stateless turn creates no durable session

- **WHEN** a turn completes without a session id
- **THEN** the system MUST NOT create a durable chat session

### Requirement: Hermetic agent tests in PR CI

New ProjectAgent tests SHALL run in PR CI with `FunctionModel` and no API key,
under the hermetic marker expression from `testing-ci`.

#### Scenario: Documented pytest invocation

- **WHEN** a developer runs `cd backend && uv run pytest tests/test_project_agent*.py -v`
- **THEN** the new agent unit/component tests SHALL execute without live credentials
