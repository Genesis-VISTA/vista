## 1. Shared harness

- [ ] 1.1 Create `backend/tests/harness/` (or `fakes/`) with in-process fake MCP toolset stubs for `rag_search`, `submit_hpc_job`, `display_file`, optional `run_bash`
- [ ] 1.2 Add helpers to build minimal `Project` + `User` in the in-memory DB session
- [ ] 1.3 Add helper to construct `ProjectAgent` with `FunctionModel` + fake toolset (no real STDIO/HTTP MCP)
- [ ] 1.4 Document how to opt into real MCP servers for integration tests later

## 2. ProjectAgent unit tests

- [ ] 2.1 Add `test_project_agent_prompt.py` asserting base + project prompt, KB slugs, and skills block
- [ ] 2.2 Add table-driven `_tool_allowed` allow/deny tests in `test_project_agent_tools.py`
- [ ] 2.3 Assert `_build_vista_metadata` token fields + `project_paths` shape (extend `test_mcp_invoke.py` patterns)

## 3. One-turn component tests

- [ ] 3.1 Add `test_project_agent_turn.py`: allowed tool → final text; assert invoke + `ProjectAgentResult` OK
- [ ] 3.2 Script runaway tool requests; assert stop at project `usage_limits.request_limit`
- [ ] 3.3 Drive elicitation / tool-approval callbacks; assert `McpElicitationEvent` / `McpToolApprovalEvent` without VISTAGuard

## 4. HTTP / SSE API

- [ ] 4.1 Add `test_agent_api.py` with `httpx.AsyncClient` + dependency overrides for fake pool / scripted `run_stream`
- [ ] 4.2 Assert SSE event kinds for a scripted turn
- [ ] 4.3 Assert non-member → 403 and missing project → 404 on `POST /projects/{name}/agent/run`

## 5. Chat session integration

- [ ] 5.1 Assert completed turn with `chat_session_id` appends history
- [ ] 5.2 Assert stateless turn does not invent a durable session
- [ ] 5.3 Prefer one HTTP-level or `ProjectAgent.run` test that closes the loop (build on `test_chat_sessions.py`)

## 6. Acceptance

- [ ] 6.1 Confirm PR CI runs new agent tests with `FunctionModel` (no API key)
- [ ] 6.2 Verify `cd backend && uv run pytest tests/test_project_agent*.py tests/test_agent_api.py -v --tb=short -m "not live and not hpc and not sandbox"`
- [ ] 6.3 Confirm no VISTAGuard gate tests were added
