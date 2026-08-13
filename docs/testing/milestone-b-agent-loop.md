# Milestone B — ProjectAgent loop + API

## Objective

The main chat agent (`ProjectAgent`) is hermetically testable with a scripted
LLM (`FunctionModel`). The HTTP/SSE contract for agent runs is locked so
regressions in streaming, authz, and history persistence fail CI.

**Status:** In review —
[!102](https://gitlab.com/amsc2/genesis/vista/-/merge_requests/102) (`f080945`).
72 tests added, no production code changed.

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

New: [`backend/tests/harness/`](../../backend/tests/harness/)

- [x] In-process fake MCP toolset ([`fake_mcp.py`](../../backend/tests/harness/fake_mcp.py))
  exposing stubs for `rag_search`, `submit_hpc_job` + `get_hpc_job_status`
  (dry-run-shaped summary strings), `display_file`, and `run_bash`, with a
  call log for assertions
- [x] `Project` / `User` builders, in-memory and DB-backed
  ([`factories.py`](../../backend/tests/harness/factories.py))
- [x] `agent_under_test` ([`agent.py`](../../backend/tests/harness/agent.py))
  constructs `ProjectAgent` and swaps in the scripted model + fake toolset via
  `Agent.override`
- [x] Scripted-LLM helpers ([`scripted.py`](../../backend/tests/harness/scripted.py))
- [x] HTTP helpers ([`api.py`](../../backend/tests/harness/api.py)) — app client
  with DB + agent-pool overrides, and an SSE parser
- [x] Opting into real MCP servers is documented in `fake_mcp.py`'s module docstring

Two things are worth knowing before extending the harness:

- **`run_stream` does not require entering the agent.** It calls
  `self.agent.run_stream_events` directly, so a turn runs without
  `async with agent:` — which is what keeps the STDIO `dev_mcp_server` and the
  HTTP `vista_mcp_server` out of these tests. No production seam was needed to
  inject the model.
- **`Agent.override(toolsets=...)` replaces the *filtered* toolsets.** The
  project's tool allow/deny patterns silently stop applying unless the harness
  re-applies `_tool_allowed`, which `agent_under_test` does. Skipping that step
  makes the tool-filter tests vacuous.
- **`FunctionModel` needs a `stream_function`.** The plain `FunctionModel(driver)`
  used by [`test_campaign_driver.py`](../../backend/tests/test_campaign_driver.py)
  works only for `agent.run()`; `scripted_model` adapts a `ModelResponse`-returning
  driver into both forms.

### 2. Unit tests on `ProjectAgent`

Target: [`backend/src/vista_backend/agents/agents.py`](../../backend/src/vista_backend/agents/agents.py)

New: [`test_project_agent_prompt.py`](../../backend/tests/test_project_agent_prompt.py) (8 tests),
[`test_project_agent_tools.py`](../../backend/tests/test_project_agent_tools.py) (30 tests)

- [x] **System prompt assembly** — base text, project `system_prompt`, KB slug
  list (and the "no KBs" message), skills XML with the `/mnt/skills` container
  path, and layer ordering. Asserted against the `SystemPromptPart` the model
  actually receives, so the `_build_agent` wiring is covered too.
- [x] **`_tool_allowed`** — table-driven fnmatch allow / deny cases, plus the
  `rag_search` auto-deny when a project has no KB
- [x] **`_build_vista_metadata`** — `project_paths` shape, per-(project, user)
  scoping, HPC-only credential attachment, and that non-HPC tools never see
  the user's tokens
- [x] The filter as the model sees it — denied tools are absent from
  `AgentInfo.function_tools`

Node ids:

- `test_project_agent_prompt.py::test_system_prompt_composes_all_layers_in_order`
- `test_project_agent_tools.py::test_tool_allowed_patterns`
- `test_project_agent_tools.py::test_vista_metadata_includes_project_paths`
- `test_project_agent_tools.py::test_vista_metadata_withholds_credentials_from_non_hpc_tools`

### 3. Component: one-turn agent

New: [`test_project_agent_turn.py`](../../backend/tests/test_project_agent_turn.py) (18 tests)

- [x] Scripted tool call → final text; asserts the tool ran, the
  `ProjectAgentResult` usage / messages are right, and that
  `FunctionToolCallEvent` / `FunctionToolResultEvent` / per-tool `LogEvent`s
  are emitted
- [x] A model that names a denied tool never reaches the tool
- [x] `run()` and `run_stream()` agree; message history is replayed
- [x] **UsageLimits**: `request_limit` and `tool_calls_limit` bound a model
  that never stops calling tools (`UsageLimitExceeded`); an unlimited project
  completes
- [x] **Elicitation / tool-approval events**: form and URL elicitation reach
  the stream and resolve; a decline is reported back to the tool;
  `cancel_elicitations` releases a waiting tool; approval emits
  `McpToolApprovalEvent` and fails closed with no approval channel. Gates stay
  off — only the plumbing that exists without the sidecar is exercised.

### 4. HTTP / SSE API

New: [`test_agent_api.py`](../../backend/tests/test_agent_api.py) (16 tests)

- [x] `httpx.AsyncClient` + `ASGITransport` against the real app. Lifespan is
  not run (it pings the real `vista_mcp_server`); the DB session and agent pool
  are overridden, and a real `ProjectAgent` on a scripted model is handed back
  by the fake pool, so the route, the agent loop, and auth are all real.
- [x] SSE event kinds for a scripted turn, and that each SSE `event:` name
  matches its payload's `event_kind`
- [x] Non-member → **403**, and the agent pool is never touched
- [x] Missing project → **404**; unknown user → **401**; unknown
  `chat_session_id` → **404**; admin may run a project they are not a member of

Reuses access-control patterns from
[`test_access_control.py`](../../backend/tests/test_access_control.py).

### 5. Chat session integration

- [x] Completed turn with `chat_session_id` appends message history
  (non-streaming and streaming)
- [x] A second turn extends the existing history rather than replacing it
- [x] Stateless turn (no session id) creates no durable session, and a
  body-supplied `message_history` is still replayed to the model
- [x] Closed at the HTTP level, building on the service tests in
  [`test_chat_sessions.py`](../../backend/tests/test_chat_sessions.py)

## Acceptance criteria

- [x] PR CI runs new agent tests with `FunctionModel` (no API key) — the
  existing `backend:test` job picks them up; `conftest.py` already pins
  `VISTA_BACKEND_MODEL=test`
- [x] Documented command works:

  ```bash
  cd backend && uv run pytest tests/test_project_agent*.py -v
  ```

- [x] Tool filter and prompt composition have regression tests
- [x] Non-member cannot run the agent (403)
- [x] No VISTAGuard gate tests added

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

- The harness is reusable for skill/project wiring assertions;
  `test_system_prompt_includes_skills_block` shows the pattern for staging a
  skill on disk where `_setup_volumes` would have put it.
- Fake `rag_search` can be replaced by real RAG fixture tests in C without
  rewriting the agent turn harness.
- Sandbox still stubbed; C owns real confinement tests under `dev_mcp_server`.
- `harness/api.py` is the first HTTP-level test surface in the backend; Milestone
  C/D routes should extend it rather than rebuild an app fixture.

## Known issues surfaced (not fixed here)

These are pre-existing production deprecations that the new tests make loud in
pytest output. They are not Milestone B work, but they will break on the
PydanticAI v2 upgrade:

- `agents.py` uses `MCPServerStreamableHTTP` / `MCPServerStdio`, both removed in
  v2 in favor of `MCPToolset`
- `agents.py` iterates `run_stream_events(...)` directly instead of
  `async with ... as stream`
- `agents.py` calls `event.result.usage()`, now a property
