## 1. Crypto

- [ ] 1.1 Add `backend/tests/test_crypto.py` Fernet round-trip for column type encrypt/decrypt
- [ ] 1.2 Assert HPC token fields stored encrypted at rest (raw DB ≠ plaintext; ORM returns plaintext)
- [ ] 1.3 Fixture sets ephemeral `VISTA_BACKEND_ENCRYPTION_KEY` in conftest if needed

## 2. Skills / seed projects

- [ ] 2.1 Add skills prompt tests (SKILL.md load + skills block in system prompt; prefer B harness)
- [ ] 2.2 Assert missing/unknown skill slug → clear error at agent build or run setup
- [ ] 2.3 Assert project `tools` / `skills` / `knowledge_bases` change tool list and prompt
- [ ] 2.4 Add seed snapshot tests for `alloy-design` and `molten-salt` against `seed.py` / `defaults.py`

## 3. RAG

- [ ] 3.1 Add mini KB fixture under `mcp_servers/vista_mcp_server/tests/fixtures/kb/` (no HF downloads in CI)
- [ ] 3.2 Introduce minimal mock-embedder seam in `rag_mcp.py` only if required
- [ ] 3.3 Add `test_rag_search.py`: known query chunk, unknown slug error, empty corpus behavior
- [ ] 3.4 Mark network/HF rebuild scripts as non-CI helpers if kept

## 4. Sandbox (`dev_mcp_server`)

- [ ] 4.1 Test `create_file` path confinement outside allowed roots
- [ ] 4.2 Test escape attempts (`../`, absolute paths outside volume) fail
- [ ] 4.3 Unit-test `run_bash` arg/cwd confinement via fake executor (PR CI)
- [ ] 4.4 Mark real microsandbox integration `@pytest.mark.sandbox` and leave `allow_failure` until reliable
- [ ] 4.5 Extend `backend/tests/security/test_tenant_isolation.py` for cross-session volume isolation

## 5. UI Vitest + CI

- [ ] 5.1 Add Vitest config + npm scripts (`test`, `test:watch`)
- [ ] 5.2 Unit-test `ui/lib/agent-events.ts` and `ui/lib/chat-session.ts` (and related pure helpers)
- [ ] 5.3 Add `ui:test` to `.gitlab-ci.yml` and `scripts/ci-local.sh` (`ui test` no longer a no-op)
- [ ] 5.4 Do not add Playwright in this milestone

## 6. Acceptance

- [ ] 6.1 Confirm RAG + skills/seed + crypto green in PR CI
- [ ] 6.2 Confirm sandbox unit subset green without live microsandbox
- [ ] 6.3 Confirm `ui:test` runs Vitest for `ui/lib`
- [ ] 6.4 Confirm no VISTAGuard / G3 policy tests
