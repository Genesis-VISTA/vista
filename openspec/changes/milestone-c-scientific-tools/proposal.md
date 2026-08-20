## Why

Non-LLM scientific tool paths (RAG, sandbox confinement, skills/project wiring,
token crypto) and thin UI lib helpers lack hermetic coverage. Milestone C closes
those gaps in PR CI without live LLM, real HPC, or Playwright E2E.

## What Changes

- Mini RAG fixture + `rag_search` tests (no Hugging Face downloads in CI)
- `dev_mcp_server` path confinement tests; fake-executor `run_bash` policy in PR CI
- Skills / project wiring and seed project snapshot assertions
- Fernet crypto round-trip for sensitive user HPC token fields
- Vitest for `ui/lib/*` plus required `ui:test` CI job
- Explicitly exclude VISTAGuard / G3 RAG policy gates and Playwright

## Capabilities

### New Capabilities

- `scientific-tools-testing`: Hermetic RAG, sandbox, skills/seed, crypto, and UI lib unit-test requirements

### Modified Capabilities

- (none)

## Impact

- `mcp_servers/vista_mcp_server` (RAG fixture seam + tests)
- `mcp_servers/dev_mcp_server` (confinement + fake executor)
- `backend/tests` (skills, seed, crypto, tenant isolation extensions)
- `ui/` (Vitest) and `.gitlab-ci.yml` / `scripts/ci-local.sh` (`ui:test`)
- Prefers Milestone B harness for skill → prompt assertions when available
