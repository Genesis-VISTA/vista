# Milestone B — ProjectAgent loop + API

## Objective

The main chat agent (`ProjectAgent`) is hermetically testable with a scripted
LLM (`FunctionModel`). The HTTP/SSE contract for agent runs is locked so
regressions in streaming, authz, and history persistence fail CI.

## In scope

- Shared test harness (fake MCP toolset + `FunctionModel` helpers)
- Unit tests for prompt assembly, tool allow/deny, vista metadata
- One-turn component tests (tool call → final answer, usage limits, elicitation
  / approval **plumbing** already on `ProjectAgent`)
- FastAPI HTTP/SSE smoke for `POST /projects/{name}/agent/run`
- Chat session history append on completed turns

## Out of scope

- Live LLM quality / golden prompt scoring (Milestone D)
- RAG correctness (Milestone C)
- Docker / microsandbox execution (Milestone C)
- UI Playwright (Milestone D)
- **VISTAGuard** (roadmap-wide out of scope — do not test gates; only MCP
  elicitation / tool-approval event plumbing that exists without the sidecar)

## Dependencies

- Milestone A markers and hermetic CI habits (not strictly blocked on HPC fakes).
- Patterns to copy:
  - [`backend/tests/test_campaign_driver.py`](../../backend/tests/test_campaign_driver.py)
    (`FunctionModel` scripted tool calls)
  - [`backend/tests/test_mcp_invoke.py`](../../backend/tests/test_mcp_invoke.py)
    (metadata shape)
  - [`backend/tests/test_chat_sessions.py`](../../backend/tests/test_chat_sessions.py)

## Work items

### 1. Shared harness

New: `backend/tests/harness/` (or `backend/tests/fakes/`)

- [ ] In-process fake MCP toolset exposing stubs for:
  - `rag_search`
  - `submit_hpc_job` (return dry-run-like summary strings)
  - `display_file`
  - optional `run_bash` stub
- [ ] Helpers to build a minimal `Project` + `User` in the in-memory DB session
- [ ] Helper to construct `ProjectAgent` (or a thin agent under test) with
  `FunctionModel` and the fake toolset — avoid launching real STDIO
  `dev_mcp_server` / HTTP `vista_mcp_server` in unit tests
- [ ] Document how to opt into real MCP servers for integration tests later

### 2. Unit tests on `ProjectAgent`

Target: [`backend/src/vista_backend/agents/agents.py`](../../backend/src/vista_backend/agents/agents.py)

New files e.g. `backend/tests/test_project_agent_prompt.py`,
`backend/tests/test_project_agent_tools.py`

- [ ] **System prompt assembly**
  - Includes base text from
    [`base_system_prompt.md`](../../backend/src/vista_backend/agents/base_system_prompt.md)
  - Appends project `system_prompt`
  - Lists KB slugs when configured
  - Includes skills XML / block from skill loader
- [ ] **`_tool_allowed`** — table-driven fnmatch allow / deny cases
- [ ] **`_build_vista_metadata`** — asserts user token fields + volume /
  `project_paths` shape (extend
  [`test_mcp_invoke.py`](../../backend/tests/test_mcp_invoke.py) patterns)

Suggested node ids:

- `test_project_agent_prompt.py::test_system_prompt_includes_project_and_skills`
- `test_project_agent_tools.py::test_tool_allowed_deny_patterns`
- `test_project_agent_tools.py::test_vista_metadata_includes_project_paths`

### 3. Component: one-turn agent

New: `backend/tests/test_project_agent_turn.py`

- [ ] `FunctionModel` script: call an allowed tool → produce final text;
  assert tool invoked and `ProjectAgentResult` OK
- [ ] **UsageLimits**: scripted model that keeps requesting tools; turn stops
  at project `usage_limits` (default / configured `request_limit`)
- [ ] **Elicitation / tool-approval events**: drive callbacks already wired on
  `ProjectAgent`; assert stream event types (`McpElicitationEvent`,
  `McpToolApprovalEvent`) without enabling VISTAGuard gates

### 4. HTTP / SSE API

New: `backend/tests/test_agent_api.py` (name as fits existing `api/` layout)

- [ ] Use `httpx.AsyncClient` + FastAPI app with dependency overrides
  (fake agent pool / scripted `run_stream`)
- [ ] Assert SSE event kinds for a scripted turn
- [ ] Non-member of project → **403** on
  `POST /projects/{name}/agent/run`
- [ ] Missing project → **404**

Reuse access-control patterns from
[`backend/tests/test_access_control.py`](../../backend/tests/test_access_control.py).

### 5. Chat session integration

- [ ] Completed turn with `chat_session_id` appends message history
- [ ] Stateless turn (no session id) does not invent a durable session
- [ ] Build on service tests in
  [`test_chat_sessions.py`](../../backend/tests/test_chat_sessions.py);
  prefer one HTTP-level or `ProjectAgent.run` integration test that closes
  the loop

## Acceptance criteria

- [ ] PR CI runs new agent tests with `FunctionModel` (no API key)
- [ ] Documented command works:

  ```bash
  cd backend && uv run pytest tests/test_project_agent*.py -v
  ```

- [ ] Tool filter and prompt composition have regression tests
- [ ] Non-member cannot run the agent (403)
- [ ] No VISTAGuard gate tests added

## How to run

```bash
cd backend
uv sync --frozen --dev
uv run pytest tests/test_project_agent*.py tests/test_agent_api.py -v --tb=short \
  -m "not live and not hpc and not sandbox"
```

## Sequencing

1. Harness (fake toolset + FunctionModel helpers)
2. Prompt / tool-filter / metadata unit tests
3. One-turn component tests
4. HTTP/SSE + authz
5. Chat session persistence on turn complete

## Handoff to Milestone C

- Harness can be reused for skill/project wiring assertions (prompt contains
  skill X when project lists it).
- Fake `rag_search` can be replaced by real RAG fixture tests in C without
  rewriting the agent turn harness.
- Sandbox still stubbed; C owns real confinement tests under `dev_mcp_server`.
