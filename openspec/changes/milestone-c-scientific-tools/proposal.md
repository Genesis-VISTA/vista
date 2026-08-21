## Why

Non-LLM scientific tool paths (RAG, sandbox volume-mount boundary, skills/project
wiring, token crypto) and thin UI lib helpers lack hermetic coverage. Milestone C
closes those gaps in PR CI without live LLM, real HPC, or Playwright E2E.

## What Changes

- Mini RAG fixture + `rag_search` tests (no Hugging Face downloads in CI)
- `dev_mcp_server` volume-mount boundary tests (approach 1a — do not invent a path jail)
- Skills / project wiring and seed project snapshot assertions
- Fernet crypto round-trip for sensitive user HPC token fields
- Vitest for `ui/lib/*` plus required `ui:test` CI job
- Explicitly exclude VISTAGuard / G3 RAG policy gates and Playwright
- Delivered across three focused MRs (backend → MCP → UI)

## Capabilities

### New Capabilities

- `scientific-tools-testing`: Hermetic RAG, sandbox volume-boundary, skills/seed, crypto, and UI lib unit-test requirements

### Modified Capabilities

- (none)

## Impact

- `mcp_servers/vista_mcp_server` (RAG fixture seam + tests)
- `mcp_servers/dev_mcp_server` (volume-mount wiring + advisory live escape tests)
- `backend/tests` (skills, seed, crypto, tenant isolation extensions)
- `ui/` (Vitest) and `.gitlab-ci.yml` / `scripts/ci-local.sh` (`ui:test`)
- Prefers Milestone B harness for skill → prompt assertions when available
