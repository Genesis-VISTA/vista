## Why

Every fresh VISTA install, dev checkout and prebuilt package seeds the same two science
projects — `molten-salt` and `alloy-design` — and the package ships the molten-salt corpus
and MSTDB to everyone who receives it. We want VISTA's out-of-the-box experience to be an
**AI Safety in Autonomous Labs** project with its own small literature corpus, and we want
the molten-salt data out of the packages we hand out, while keeping the science projects
one switch away for development and for builds that need them.

## What Changes

- New backend setting `VISTA_BACKEND_SEED_SCIENCE_PROJECTS` (default off). When on, dev
  seeding creates `molten-salt` and `alloy-design` and fetches their corpus and MSTDB assets
  exactly as today. When off, it does not.
- New default project `ai-safety-autonomous-labs` (fixed UUID), seeded always: its own
  system prompt and description, no mandated skills, tools `["*", "!agenthpc_*"]` (HPC job
  submission allowed, so the agent can discover and run the example jobs), `request_limit`
  50.
- New knowledge base `ai-safety` ("AI Safety Papers"), sourced from vista-data's
  `ai-safety/` folder (three papers: the autonomous-science-labs roadmap, the survey of
  autonomy-induced security risks in LLM agents, and the autonomous-driving security
  review). Without vista-data access the project is seeded without it and a warning is
  logged.
- Seeding from a bundled payload seeds **what the payload contains**: the AI-safety KB when
  `ai-safety/` is present, the science projects only when `molten-salt-papers/` and `mstdb/`
  are. The flag is not consulted at package runtime.
- New additive boot-time sync of default projects: inserts the AI-safety project and KB by
  UUID where missing on existing databases, attaches the KB to the project once it exists,
  and never overwrites or deletes anything. Legacy projects and data on existing installs
  are left alone.
- Skills that need MSTDB assets (`salt-analysis`, `salt-prediction`, `model-fine-tuning`)
  are skipped when MSTDB is unavailable, as they already are without a token. All other
  bundled skills are still registered regardless of the flag.
- Package launchers (`package_launcher.sh`, `package_launcher.ps1`) install missing corpora
  per corpus rather than per top-level payload part, so an upgraded install receives the
  AI-safety corpus even though its `vista-data/` and `knowledge-bases/` already exist.
- **BREAKING (packaging)**: `build_local_package.sh` always requires and packs only the
  AI-safety corpus and its index. The molten-salt corpus, MSTDB and the `forge-tune` CSV
  are packed only with the new `--science-projects` option. `--vector-store DIR` now reuses
  a prebuilt **AI-safety** index; the molten-salt index moves to the new
  `--science-projects-vector-store DIR`. Both are optional and only for skipping
  re-embedding.
- Package smoke test and the live UI smoke test target the new project and KB; the smoke
  test also asserts a default build carries no MSTDB or molten-salt corpus.

## Capabilities

### New Capabilities
- `default-projects`: which projects and knowledge bases VISTA seeds by default, the
  science-projects switch, payload-driven seeding, and the additive sync that brings
  defaults to existing databases.

### Modified Capabilities
- `laptop-distribution`: the bundled, pre-indexed corpus becomes the AI-safety corpus
  (science corpus optional at build time, MSTDB not distributed by default); first-run setup
  installs any missing corpus on upgraded installs; build-time verification queries the
  AI-safety corpus and checks the science data is absent from default builds.

## Impact

- **Backend**: `backend/src/vista_backend/config.py` (new setting),
  `backend/src/vista_backend/db/seed.py` (split seeding, new project/KB, sync),
  `backend/src/vista_backend/db/db.py` (call the sync on boot), new
  `db/system_prompts/ai-safety-autonomous-labs.md`.
- **Packaging**: `scripts/build_local_package.sh`, `scripts/package_launcher.sh`,
  `scripts/package_launcher.ps1`, `scripts/smoke_test_package.sh`.
- **Tests**: `backend/tests/test_seed_projects.py`, `test_seed_knowledge_base.py`,
  `test_seed_payload.py`, `test_seed_splash.py`, new sync tests; `ui/e2e/smoke.spec.ts`.
- **Docs**: `README.md` (build options), `docs/project-onboarding.md` (stale default-project
  section), `docs/validation-lane.md`.
- **Data**: vista-data `ai-safety/` (already committed). No MCP server change —
  `rag_search` already discovers any `knowledge_bases_dir/<slug>/rag_db`.
- **Out of scope**: per-project HPC job scoping, removing legacy data from existing installs,
  VISTAGuard's `g3_kb_policy.json`.
