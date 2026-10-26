## 1. Backend: detect, refuse and index

- [x] 1.1 Port `backend/src/vista_backend/db/schema_check.py` and `backend/tests/test_schema_check.py` from `db-migrations` (`bae3f97b`) unchanged. Verify with `cd backend && uv run --extra dev pytest tests/test_schema_check.py -q`.
- [x] 1.2 In `db/db.py`, add `DatabaseIncompatible`, the code constant `DATABASE_INCOMPATIBLE = "database-incompatible"`, and the pre-engine check of D2 in `init_db`. Add `backend/tests/test_incompatible_database.py`, which verifies:
  - a fresh and a missing database start and seed;
  - an additive gap (a missing nullable column or table) starts and is filled in;
  - an extra nullable column starts;
  - a missing NOT NULL column and an unwritten NOT NULL column each raise before any write, with the database's bytes unchanged and a message naming the column, the path and the remedy.
- [x] 1.3 Write `incompatible-data-index.json` beside a refused database (D6), listing projects (id, name), skills (name, path) and knowledge bases (slug, name), each query guarded. Verify in the same test file that:
  - the index lists what the database holds;
  - a database missing the `skill` table still gets an index without skills;
  - the database's bytes are unchanged.
- [x] 1.4 Copy `backend/tests/fixtures/db/v0.1.4.sql` from `db-migrations` (`4caf8a99`), with a comment in the test saying where it came from. Verify that `init_db` on it raises `DatabaseIncompatible` naming `chat_session.user_id` and `skill.is_public`, writes an index listing its four projects, its skills and its three knowledge bases, and leaves the database unchanged.
- [x] 1.5 In `api/api.py`'s lifespan, log a `DatabaseIncompatible`, write its code to `VISTA_STARTUP_FAILURE_FILE` when set, and re-raise (ported from `dadfdd15`). Verify with `backend/tests/test_startup_refusal.py`: with the variable set, the file holds `database-incompatible`; without it, no file is written. Either way it raises.
- [x] 1.6 Run `./scripts/ci-local.sh backend` and verify it is green.
- [x] 1.7 Mark each index entry with `comes_with_vista` (D6): a skill named after a bundled skill in `db/skills/`, or a project or knowledge base whose id seeding creates (`db/seed.py`). Verify in `test_incompatible_database.py` that the v0.1.4 fixture's bundled skills and seeded projects and knowledge bases are marked, `fixture-project` and `fixture-kb` are not, and the bytes are still unchanged; then `./scripts/ci-local.sh backend`.

## 2. Launchers: report an exited service at once

- [x] 2.1 Port the `wait_for` / `Wait-For` PID check and `VISTA_STARTUP_FAILURE_FILE` handling to `scripts/package_launcher.sh` and `.ps1` from `dadfdd15`. Port the supervised-test cases with `database-incompatible` as the code. Verify with `./scripts/ci-local.sh launcher`, including the PowerShell run under pwsh, and `./scripts/ci-local.sh install lint`.

## 3. Startup window: Back up and reset

- [x] 3.1 Add `'database-incompatible': 'Your local data is incompatible with this version of VISTA, please reset.'` to `FAILURE_MESSAGES` in `electron/src/startup-protocol.js`. Make the failure code reachable from the renderer's snapshot if it isn't already. Extend `electron/test/startup-protocol.test.js` to read the backend's code constant. Verify with `cd electron && npm test`.
- [x] 3.2 Add `electron/src/data-reset.js` (D5, D6). Given the state directory and a clock, it creates `backups/<UTC>/` and moves every top-level entry except the frozen keep-list (`backups`, `logs`, `huggingface`, `microsandbox`, `vista-data`) into it. It writes `README.txt` from `incompatible-data-index.json`, or from the disk without one. It returns the backup path and what moved, and on a failed rename it moves everything back. Add `electron/test/data-reset.test.js` on a temp directory, covering:
  - what moves and what stays, including an unknown entry being moved;
  - a second reset beside an earlier backup;
  - rollback when a rename fails;
  - the README with the index (names the projects, skills and knowledge bases and where they are) and without it (skills named from `SKILL.md`, projects by id).

  Verify with `cd electron && npm test`.
- [x] 3.3 Wire `startup:reset-data` in `main.js`, with the `fromStartupWindow` guard. It acts only when the current failure code is `database-incompatible` and the launcher has stopped. It shows the confirmation, with Cancel as the default, then runs `data-reset.js`. It then shows the backup-location message with Show Folder and Continue, then retries. A failure stays on screen and is logged to `window.log`. Add `resetData()` to `startup-preload.cjs`. Verify the guard conditions with unit tests wherever the existing tests reach them, and with `./scripts/ci-local.sh electron`.
- [x] 3.4 Add the Back up and reset button to `startup.html` and `startup-renderer.js`, shown only for `database-incompatible`, which also hides Try Again. Verify with a renderer test if the existing tests cover the renderer. Otherwise verify by hand in 4.1.

- [x] 3.5 Rework `writeReadme` (D6): list only entries whose folder exists in the backup, the researcher's own first, and VISTA's own (`comes_with_vista`) after a line saying they come back with VISTA. Verify in `data-reset.test.js`: own items first, VISTA's separate, a listed folder that's absent is left out, and without an index everything found on disk is listed as the researcher's.
- [x] 3.6 Open the README with the restore paragraph and the coding-agent prompt from D6, verbatim, with the backend port from `VISTA_BACKEND_PORT` (default 8001), passed in from `main.js`. Verify in `data-reset.test.js` that the README begins with the prompt, which names the API URL and carries each rule (backup unchanged, API only, own items only, no credentials, confirm first); then `./scripts/ci-local.sh electron`.

## 4. Docs and manual validation

- [x] 4.1 Manual, not in PR CI. Build this branch with `scripts/build_local_package.sh`, and start it with `VISTA_HOME` pointing at a scratch folder holding a v0.1.4 database (loaded from `v0.1.4.sql`, or a copy of a real v0.1.4 install), plus some skills, a knowledge base and project files. Verify that:
  - the startup window shows the message within seconds, with Back up and reset and no Try Again;
  - Cancel moves nothing;
  - Back up and reset moves everything but the caches and logs into `backups/<UTC>/`, names the folder, and Show Folder opens it;
  - the README names the projects, skills and knowledge bases and where they are;
  - VISTA then opens as on a first run, with the bundled knowledge bases searchable and no download.
- [x] 4.2 Add one line to `AGENTS.md` under Backend, using the `writing-for-agents` skill: a schema change that isn't additive (a dropped, renamed or retyped column, or a NOT NULL column without a server default) makes every existing install prompt the researcher to back up and reset. Prefer additive changes. Verify the line is there and `./scripts/ci-local.sh docs` passes.
- [x] 4.3 Add a line to `openspec/changes/single-user-backend/` (its proposal BREAKING bullet and design D4) saying that this change replaces "delete it by hand" with the reset prompt. Verify that `openspec validate --all` passes.
- [ ] 4.4 Manual, not in PR CI. Rebuild the package, and repeat the 4.1 reset on a fresh copy of the scratch data. Check that the README lists `fixture-project`, `Fixture KB` and the two skill folders first, lists VISTA's own separately, and lists no absent folder. Then, with the fresh VISTA running, paste the README's prompt into Claude Code in the backup folder. Check that it proposes a plan, waits for a go-ahead, and then restores `Fixture KB` with its PDF and `fixture-project` with `result.csv`, through the API, leaving the backup unchanged.
