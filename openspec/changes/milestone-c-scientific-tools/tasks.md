## 1. Crypto

- [x] 1.1 Add `backend/tests/test_crypto.py` Fernet round-trip for column type encrypt/decrypt
- [x] 1.2 Assert HPC token fields (S3M / NERSC IRI / Globus) stored encrypted at rest (raw DB ≠ plaintext; ORM returns plaintext)
- [x] 1.3 Fixture sets ephemeral `VISTA_BACKEND_ENCRYPTION_KEY` and clears `get_fernet` cache

## 2. Skills / seed projects

- [x] 2.1 Add skills prompt tests (SKILL.md load + skills block in system prompt; use B harness)
- [x] 2.2 Assert missing/unknown skill slug → skipped with a warning (current production behavior — do not invent a hard error)
- [x] 2.3 Assert project `tools` / `skills` / `knowledge_bases` change tool list and prompt
- [x] 2.4 Add seed snapshot tests for `alloy-design` and `molten-salt` against `seed.py` (there is no `defaults.py` — seed data lives inline)

## 3. RAG

- [ ] 3.1 Add mini KB fixture under `mcp_servers/vista_mcp_server/tests/fixtures/kb/` (no HF downloads in CI)
- [ ] 3.2 Introduce minimal mock-embedder seam in `rag_mcp.py` only if required
- [ ] 3.3 Add `test_rag_search.py`: known query chunk, unknown slug error, empty corpus behavior
- [ ] 3.4 Mark network/HF rebuild scripts as non-CI helpers if kept

## 4. Sandbox (`dev_mcp_server`)

Committed approach (1a): there is no application-level path jail today —
`create_file` / `view` / `run_bash` pass paths straight to the guest; the
security boundary is the volume mount. Do **not** invent a path-jail feature.

- [ ] 4.1 Unit-test volume-mount wiring (`VISTA_DEV_MCP_VOLUMES` → sandbox spawn args) without a live daemon
- [ ] 4.2 Document in the test module that the VM/mount is the security boundary
- [ ] 4.3 Escape attempts against a **real** container marked `@pytest.mark.sandbox` and left `allow_failure` until the daemon is reliable
- [ ] 4.4 Extend `backend/tests/security/test_tenant_isolation.py` for cross-session volume isolation where cheap

## 5. UI Vitest + CI

- [ ] 5.1 Add Vitest config + npm scripts (`test`, `test:watch`)
- [ ] 5.2 Unit-test `ui/lib/agent-events.ts` and `ui/lib/chat-session.ts` (and related pure helpers)
- [ ] 5.3 Add `ui:test` to `.gitlab-ci.yml` and `scripts/ci-local.sh` (`ui test` no longer a no-op)
- [ ] 5.4 Do not add Playwright in this milestone

## 6. Acceptance

- [ ] 6.1 Confirm RAG + skills/seed + crypto green in PR CI
- [ ] 6.2 Confirm sandbox volume-boundary unit subset green without live microsandbox
- [ ] 6.3 Confirm `ui:test` runs Vitest for `ui/lib`
- [ ] 6.4 Confirm no VISTAGuard / G3 policy tests

## Sequencing (three MRs)

1. **MR 1 — backend:** crypto + skills/seed snapshots (this MR)
2. **MR 2 — MCP:** RAG (pure BM25/hybrid + tiny Chroma fixture, no HF download) + sandbox volume-boundary tests (1a)
3. **MR 3 — UI:** Vitest for `ui/lib/*` + `ui:test` CI job
