## Why

The main chat path (`ProjectAgent`) is only thinly tested today. Without a
hermetic harness, regressions in prompt assembly, tool allow/deny, streaming,
authz, and session persistence slip past PR CI. Milestone B makes the agent
loop and HTTP/SSE contract testable with a scripted LLM (`FunctionModel`) and
no API keys.

## What Changes

- Add a shared test harness (fake MCP toolset + `FunctionModel` helpers)
- Unit-test prompt assembly, tool filters, and vista metadata on `ProjectAgent`
- Add one-turn component tests (tool → final answer, usage limits, elicitation plumbing)
- Lock FastAPI HTTP/SSE smoke for `POST /projects/{name}/agent/run` including 403/404
- Assert chat session history appends on completed turns (and not for sessionless turns)
- Keep VISTAGuard gate testing out of scope (elicitation event plumbing only)

## Capabilities

### New Capabilities

- `project-agent-testing`: Hermetic ProjectAgent harness, one-turn loop, HTTP/SSE authz, and chat history persistence tests

### Modified Capabilities

- (none)

## Impact

- `backend/tests/` (new harness + `test_project_agent_*` / `test_agent_api` suites)
- `backend/src/vista_backend/agents/agents.py` (behavior under test; production changes only if seams needed)
- PR CI backend job (new hermetic tests; still no live LLM)
- Patterns reused from `test_campaign_driver.py`, `test_mcp_invoke.py`, `test_chat_sessions.py`, `test_access_control.py`
