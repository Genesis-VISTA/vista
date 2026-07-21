# Milestone C — Scientific tools (RAG, sandbox, skills, crypto)

## Objective

Non-LLM scientific tool paths and project wiring are validated hermetically:
RAG retrieval, sandbox confinement, skill/project configuration, token
encryption at rest, and thin UI lib unit tests.

## In scope

- Mini RAG fixture + `rag_search` tests
- `dev_mcp_server` confinement (`create_file`, path escape); sandbox-marked
  `run_bash` policy documented
- Cross-session volume isolation extensions
- Skills / project wiring (prompt + tool list)
- Seed project snapshot assertions
- Fernet crypto round-trip for sensitive user fields
- Vitest for `ui/lib/*` + CI `ui:test` job

## Out of scope

- Nightly live LLM / real HPC (Milestone D)
- Full Playwright E2E (Milestone D)
- **VISTAGuard** (including G3 RAG policy gates — out of roadmap scope;
  RAG tests here are functional retrieval only)
- Expanding campaign framework tests (already strong)

## Dependencies

- Milestone B harness preferred for skill/project → agent prompt/tool assertions.
- Milestone A CI patterns for adding `ui:test` and keeping PR hermetic.

## Work items

### 1. RAG

- [ ] Fixture mini KB under
  `mcp_servers/vista_mcp_server/tests/fixtures/kb/`
  - Prefer a **committed** tiny Chroma DB or a mock embedder so CI does **not**
    download Hugging Face models
  - If mock embedder is required, introduce the seam in
    [`rag_mcp.py`](../../mcp_servers/vista_mcp_server/src/vista_mcp_server/rag_mcp.py)
    behind a test-friendly interface (minimal production change)
- [ ] Tests:
  - [ ] `rag_search` returns the expected chunk for a known query
  - [ ] Missing / unknown KB slug → clean error
  - [ ] Empty corpus behavior documented and asserted
- [ ] Mark network/HF-dependent rebuild scripts as non-CI helpers if kept

Suggested: `mcp_servers/vista_mcp_server/tests/test_rag_search.py`

### 2. Sandbox (`dev_mcp_server`)

Extend beyond [`tests/test_view.py`](../../mcp_servers/dev_mcp_server/tests/test_view.py):

- [ ] `create_file` path confinement (cannot write outside allowed roots)
- [ ] Escape attempts (`../`, absolute paths outside volume) fail
- [ ] **`run_bash` policy (committed choice):**
  - Unit tests against a **fake executor** interface for arg/cwd confinement
    in PR CI
  - Real microsandbox integration marked `@pytest.mark.sandbox` and left
    `allow_failure` in CI until the daemon is reliable
- [ ] Cross-session isolation: extend
  [`backend/tests/security/test_tenant_isolation.py`](../../backend/tests/security/test_tenant_isolation.py)

### 3. Skills / projects

- [ ] SKILL.md load + skills block appears in agent system prompt (use B harness)
- [ ] Missing / unknown skill slug → clear error at agent build or run setup
- [ ] Project `tools` / `skills` / `knowledge_bases` change tool list and prompt
- [ ] Seed projects `alloy-design` and `molten-salt` snapshot assertions against
  [`seed.py`](../../backend/src/vista_backend/db/seed.py) /
  [`defaults.py`](../../backend/src/vista_backend/db/defaults.py)
  (expected skill slugs, tool patterns)

Suggested: `backend/tests/test_skills_prompt.py`,
`backend/tests/test_seed_projects.py`

### 4. Crypto

Target: [`backend/src/vista_backend/utils/crypto.py`](../../backend/src/vista_backend/utils/crypto.py)

- [ ] Fernet column type round-trip encrypt / decrypt
- [ ] User HPC token fields (S3M / NERSC IRI, etc.) stored encrypted at rest
  (read raw DB value ≠ plaintext; ORM read returns plaintext)
- [ ] Tests set `VISTA_BACKEND_ENCRYPTION_KEY` via fixture (generate ephemeral
  Fernet key in conftest if needed)

Suggested: `backend/tests/test_crypto.py`

### 5. UI (thin) — Vitest

**Committed approach:** add **Vitest** for `ui/lib/*` only.

- [ ] Add Vitest config + npm scripts (`test`, `test:watch`)
- [ ] Unit tests for:
  - [`ui/lib/agent-events.ts`](../../ui/lib/agent-events.ts) — event parsing
  - Chat session helpers in [`ui/lib/chat-session.ts`](../../ui/lib/chat-session.ts)
    (and related pure helpers)
- [ ] Add `ui:test` job to [`.gitlab-ci.yml`](../../.gitlab-ci.yml) and
  [`scripts/ci-local.sh`](../../scripts/ci-local.sh) (`ui test` no longer a no-op)
- [ ] Do **not** add Playwright in this milestone

## Acceptance criteria

- [ ] RAG + skill/project wiring + crypto tests green in PR CI
- [ ] Sandbox confinement unit subset green **without** live microsandbox
- [ ] `ui:test` job runs Vitest for `ui/lib`
- [ ] No VISTAGuard / G3 policy tests

## How to run

```bash
cd mcp_servers/vista_mcp_server
uv run --extra dev pytest tests/test_rag_search.py -v

cd mcp_servers/dev_mcp_server
uv run pytest tests/ -v -m "not sandbox"

cd backend
uv run pytest tests/test_crypto.py tests/test_skills_prompt.py tests/test_seed_projects.py -v

cd ui
npm test
```

## Sequencing

1. Crypto (small, isolated)
2. Skills / seed project snapshots (uses B harness if available)
3. RAG fixture + search tests
4. Sandbox confinement + fake executor
5. Vitest + `ui:test` CI job

## Handoff to Milestone D

- Hermetic scientific-tool coverage is in place; D adds live agent-mode and
  optional real HPC / Playwright on a scheduled pipeline only.
- Evaluation runbook tool-mode latency measurements remain separate from these
  correctness tests.
