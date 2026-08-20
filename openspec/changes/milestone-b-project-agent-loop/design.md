## Context

Milestone A established hermetic markers and CI habits. Campaign framework tests
already demonstrate `FunctionModel` scripting (`test_campaign_driver.py`). The
main `ProjectAgent` chat path still lacks an equivalent harness, so HTTP/SSE and
prompt/tool regressions are under-covered.

## Goals / Non-Goals

**Goals:**

- Hermetic ProjectAgent unit + one-turn component coverage
- Locked HTTP/SSE + authz contract for agent runs
- Chat session persistence assertions on completed turns
- Reusable harness for Milestone C skill/prompt wiring

**Non-Goals:**

- Live LLM quality / golden prompt scoring (Milestone D)
- RAG correctness, Docker/microsandbox execution (Milestone C)
- UI Playwright (Milestone D)
- VISTAGuard gates — only existing elicitation / tool-approval event plumbing

## Decisions

1. **In-process fake MCP toolset over real servers in unit tests**
   - Rationale: keeps PR CI fast and hermetic; real servers remain an opt-in later.
   - Alternative considered: spin STDIO/HTTP MCP in every test — rejected for flakiness and speed.

2. **`FunctionModel` for scripted turns**
   - Rationale: already proven in campaign tests; no API key.
   - Alternative: record/replay live traces — deferred to Milestone D soft asserts.

3. **Dependency-overridden FastAPI app for SSE**
   - Rationale: matches existing access-control test style; isolates stream contract from pool lifecycle.

4. **Elicitation events without VISTAGuard**
   - Rationale: roadmap-wide OOS for guard gates; still verify event types already wired on the agent.

## Risks / Trade-offs

- [Harness drift from production MCP wiring] → Document opt-in real-server path; keep stub signatures close to dry-run summaries
- [Over-mocking prompt assembly] → Assert against real loaders/files where cheap (`base_system_prompt.md`, skill loader)
- [SSE flake from timing] → Prefer scripted `run_stream` override over full pool concurrency

## Migration Plan

Ship as one focused MR. No production migration. After merge, Milestone C may
reuse the harness for skills/seed assertions.

## Open Questions

- Exact package path for harness: `backend/tests/harness/` vs `backend/tests/fakes/` (prefer `harness/` if both agent + MCP helpers land there)
