## 1. Science-projects switch in dev seeding (D2)

- [ ] 1.1 Add `seed_science_projects: bool = False` to backend `Settings` in `backend/src/vista_backend/config.py`, with a docstring naming `VISTA_BACKEND_SEED_SCIENCE_PROJECTS`; verify `Settings.model_validate({})` gives `False` and the env var set to `true` gives `True`
- [ ] 1.2 Add `async has(repo_path) -> bool` to `LocalRepoClient` and `GitlabRepoClient` in `db/seed.py`, and factor client selection into a shared `_vista_data_client()` helper; verify with unit tests for `LocalRepoClient.has` (present / absent / path escaping the root) and a faked-transport test for `GitlabRepoClient.has`
- [ ] 1.3 Compute `science` in `seed_db` per D2's table and gate the forge-tune CSV, the molten-salt download/index/KB row, both science projects, and the `SKILL_ASSETS` skills on it; verify `test_seed_projects.py` parametrized over the flag: off → no `molten-salt`/`alloy-design`, no MSTDB skills, all other bundled skills present; on (token faked) → today's snapshot unchanged
- [ ] 1.4 Update `test_seed_splash.py`, `test_seed_knowledge_base.py` and `test_seed_payload.py` to enable science where they assert molten-salt behavior, and add a payload test: a payload with only `ai-safety/` seeds without error and with no science projects; a payload with `molten-salt-papers/` and `mstdb/` seeds them without the flag; verify `cd backend && uv run --extra dev pytest -m "not live and not hpc and not sandbox"` passes

## 2. AI-safety default project and KB via boot sync (D1, D3, D7)

- [ ] 2.1 Draft `backend/src/vista_backend/db/system_prompts/ai-safety-autonomous-labs.md` and the project description per D7, from the three papers in vista-data `ai-safety/`; verify it reads cleanly and names no specific HPC job (Sam reviews wording)
- [ ] 2.2 Parametrize `_build_knowledge_base` / `_assert_knowledge_base_indexed` callers by KB dir (they already take one) and add `sync_default_projects(engine)`: insert the project (fixed UUID, tools `["*", "!agenthpc_*"]`, `request_limit=50`, no skills), acquire `ai-safety/` via `_vista_data_client()`, index it, insert the `ai-safety` KB row (fixed UUID, "AI Safety Papers"), append the slug to the project if missing, add the dev test user's membership on insert outside prod; verify with a `tmp_path` payload test that a fresh DB ends with the project and an attached, indexed KB
- [ ] 2.3 Implement D3's failure handling: network/token errors and a missing client warn and continue; on the payload path the sync never indexes and only asserts the bundled `ai-safety` index, raising if it is absent or empty; apply the same payload-never-indexes rule to `molten-salt-papers` in `seed_db`; verify with tests for no client (project without KB, warning logged, startup completes), a fake client raising `httpx.HTTPStatusError` (same), an empty bundled store (raises), and a payload with PDFs but no `rag_db` for each corpus (raises naming the corpus, with the indexer patched to fail the test if called)
- [ ] 2.4 Call `sync_default_projects` from `db/db.py` after `seed_db` and before `sync_bundled_skills`; verify an upgrade test: a DB pre-seeded with only the science projects gains the AI-safety project and KB and its science rows are byte-identical afterwards
- [ ] 2.5 Add sync idempotency tests: an edited system prompt survives restart; a user-attached extra KB is kept; a KB that becomes available on a later boot is created and attached; a deleted project is restored; science projects are not added to an existing DB with the flag on; verify all pass under the hermetic filter
- [ ] 2.6 Run the dev stack against your real `vista.db` with the token in `.env` (`./launch.sh logs`) and ask the new project about memory poisoning; verify the answer cites an `ai-safety` passage and the existing molten-salt projects are untouched

## 3. Package build: AI-safety always, science optional (D5)

- [ ] 3.1 Add `--science-projects` (also honoring `VISTA_BACKEND_SEED_SCIENCE_PROJECTS=true`) and `--science-projects-vector-store DIR` to `scripts/build_local_package.sh`, repoint `--vector-store` at the ai-safety store, reject `--science-projects-vector-store` without `--science-projects`, and update `--help`; verify `--check` with each combination reports the right required inputs and the invalid combination fails naming the option
- [ ] 3.2 Make preflight require `ai-safety/` always and `molten-salt-papers/` + `mstdb/` only with science; make `stage_payload` copy only the selected folders from the clone or `--payload`; copy the forge-tune CSV only with science; verify a `--check` run against a payload lacking `mstdb/` passes by default and fails with `--science-projects`
- [ ] 3.3 Parametrize `build_vector_store` / `check_store_matches_corpus` by slug and run them for `ai-safety` always and `molten-salt-papers` with science; write `payload/parts.txt` per D4 and include it in the recorded contents; verify a default build's `payload.tar` lists only `vista-data/ai-safety`, `knowledge-bases/ai-safety`, `huggingface` (`tar -tf … | cut -d/ -f1-2 | sort -u`)
- [ ] 3.4 Document the build options in `README.md`: both store options are optional and only for reusing an existing prebuilt store to skip re-embedding; `--science-projects` packs the molten-salt corpus and MSTDB; verify the README and `--help` wording match

## 4. Launchers install missing corpora per corpus (D4)

- [ ] 4.1 Change `scripts/package_launcher.sh` to read `payload/parts.txt` and extract only members missing from `$STATE`; verify by running an unpacked default package against a copy of a state dir from an earlier molten-salt package: `ai-safety` is installed, molten-salt data is untouched, and the new project's retrieval works
- [ ] 4.2 Make the same change in `scripts/package_launcher.ps1`; verify on Windows (or leave unchecked with a note in the change for the next Windows session, per the windows-support handoff)

## 5. Verification and docs (D6)

- [ ] 5.1 Point `scripts/smoke_test_package.sh`'s retrieval check at `/projects/ai-safety-autonomous-labs/mcp/call` with `kb_slug: ai-safety`, and add the no-science-data assertion for default builds; verify a full default build (`./scripts/build_local_package.sh --payload ~/.vista-build/vista-data` after pulling it) passes its smoke test
- [ ] 5.2 Verify a `--science-projects --science-projects-vector-store ~/.vista-build/rag_db` build also passes, with both corpora retrievable
- [ ] 5.3 Update `ui/e2e/smoke.spec.ts` to look for the `ai-safety-autonomous-labs` card and `docs/validation-lane.md` to match; verify `cd ui && npm run lint` passes
- [ ] 5.4 Rewrite the stale default-project sections of `docs/project-onboarding.md` (no `defaults.py`; first-run seed plus AI-safety sync; the science flag; deleted default restored); verify every file path it links exists
- [ ] 5.5 Run `./scripts/ci-local.sh` and `openspec validate ai-safety-default-project --strict`; verify both pass
