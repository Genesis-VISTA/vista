## Context

See proposal.md for motivation. The relevant current state:

- `backend/src/vista_backend/db/seed.py:seed_db` runs only against an empty project table.
  In one pass it fetches vista-data (token → `GitlabRepoClient`, payload →
  `LocalRepoClient`, neither → skip), builds the `molten-salt-papers` index, registers
  bundled skills (copying MSTDB assets into three of them), inserts the two science projects
  and one KB row with fixed UUIDs, and adds dev test users. `db/db.py` then calls
  `sync_bundled_skills` on every boot, the additive precedent this change copies.
- MCP `rag_search` discovers any `knowledge_bases_dir/<slug>/rag_db`. A project sees its KBs
  through `project.knowledge_bases`, and `rag_search` is denied to a project with none
  (`agents.py:547`). No MCP change is needed.
- `LocalRepoClient` raises on any missing path, so a payload without `mstdb/` currently
  fails seeding.
- `build_local_package.sh` copies the **whole** vista-data tree into the payload, requires
  `molten-salt-papers/` and `mstdb/`, copies the MSTDB CSV into
  `app/hpc_jobs/forge-tune/`, and indexes (or with `--vector-store`, reuses) only the
  molten-salt store.
- Both package launchers extract `payload.tar` by top-level part (`vista-data`,
  `knowledge-bases`, `huggingface`), skipping any part that exists, so an upgrade never
  receives a new corpus.
- vista-data now holds `ai-safety/` with the three PDFs (commit "Add ai-safety project").
- `electron-desktop-shell` has an open delta on laptop-distribution's "Single command to
  install and run". This change adds a separate requirement instead of modifying that one.

## Goals / Non-Goals

**Goals:**
- One seeding path for the AI-safety default that serves first run and upgrade alike.
- A default package that provably contains no molten-salt or MSTDB data.
- Science projects keep their exact current behavior when enabled.

**Non-Goals:**
- Adding science projects to an existing database when the flag is turned on later.
- Per-project HPC job scoping. The new project sees every job in `hpc_jobs/`.
- Removing legacy projects or data from existing installs.
- VISTAGuard `g3_kb_policy.json`. `ai-safety` is simply unpinned.

## Decisions

### D1. The AI-safety default is seeded by a boot-time sync, not by `seed_db`

A new `sync_default_projects(engine)` in `seed.py` runs on every boot, right after
`seed_db` and before `sync_bundled_skills`. It owns the AI-safety project and KB for both
the first run and upgrades. `seed_db` keeps only the first-run-only material: the science
projects, bundled skills and test users.

The sync:
1. inserts the project by its fixed UUID if missing;
2. if the `ai-safety` KB row is missing, tries to obtain the corpus (D3), builds the index
   and inserts the KB row with its own fixed UUID;
3. appends `ai-safety` to the project's `knowledge_bases` if the KB row exists and the slug
   is absent. Only that field changes, and only by appending;
4. outside prod, adds the dev test user's membership when it inserts the project.

The science projects stay in `seed_db`, so their empty-DB-only semantics are unchanged.

*Alternative: one generic sync for all defaults.* Rejected. An upgraded package's state
directory still holds the molten-salt payload, so the sync would re-create `molten-salt`
after every deletion. It would also start adding projects to existing DBs when the flag
flips, which nobody asked for.

*Ordering.* `seed_db`'s early return checks `count(ProjectTable) > 0`. Running it before the
sync keeps "empty DB" meaning what it does today.

### D2. Science seeding is gated by source, the flag and payload contents

```
source          science seeded when                         ai-safety corpus from
--------------  ------------------------------------------  -----------------------
payload         payload has molten-salt-papers/ AND mstdb/  payload ai-safety/
token           settings.seed_science_projects is true      GitLab ai-safety/
neither         never                                       unavailable → warn
```

Both repo clients gain `async has(repo_path) -> bool`. `LocalRepoClient` checks the path;
`GitlabRepoClient` checks via the tree endpoint. `seed_db` computes `science: bool` once
from this table. When `science` is false it skips the forge-tune CSV, the molten-salt
download, index and KB row, and the two projects. It also treats the `SKILL_ASSETS` skills
exactly like the no-token case (added to `skipped_skills`). No other skill is affected.

The setting is `seed_science_projects: bool = False` on backend `Settings`, which gives
`VISTA_BACKEND_SEED_SCIENCE_PROJECTS` through the existing prefix.

### D3. Corpus acquisition in the sync is best-effort, and a bad bundled index is fatal

The sync opens the same client that `seed_db` chooses (a shared `_vista_data_client()`
helper). It downloads `ai-safety/` to `knowledge_bases_dir/ai-safety/pdfs`, and then calls
the existing `_build_knowledge_base` and `_assert_knowledge_base_indexed`, parametrized by
KB dir.

- **Network or token failure** (`httpx` errors, 401/404, GitLab 5xx): log a warning and
  continue without the KB. The next boot retries. This keeps zero-config startup intact:
  this very session hit a revoked token and a GitLab 502.
- **No client** (no token, no payload): warn once per boot while the KB is missing.
- **A payload whose `ai-safety/` index is absent or empty**: raise, as today. That's a
  packaging defect, and the laptop-distribution spec requires setup to fail on it.
- **Citations**: `_build_knowledge_base` already passes `citation_credentials()`, and the
  indexer skips citation extraction when there are none. So a dev boot with no LLM
  credentials indexes text only instead of failing. Three papers take about a minute.

### D4. Launchers install missing parts per corpus, driven by a manifest

The build writes `payload/parts.txt`, one tar member directory per line:
- `vista-data/ai-safety`, `knowledge-bases/ai-safety` and `huggingface`;
- with `--science-projects`, also `vista-data/molten-salt-papers`, `vista-data/mstdb` and
  `knowledge-bases/molten-salt-papers`.

Both launchers read it and extract only the members missing from `$STATE`. The tar layout
is unchanged, so `tar -xf payload.tar <members…>` works as it does today with a finer list.
`vista-data/README.md` is no longer packed.

*Alternative: `tar -t` at launch.* Rejected. It lists every file in a large tar on every
run, and a manifest is the explicit contract between build and launcher. A launcher always
ships with its own payload, so old manifests never meet new launchers.

### D5. Build options

| Option | Meaning |
|---|---|
| *(always)* | Requires `ai-safety/` in the vista-data source and packs only that folder. Indexes it, or reuses a store. |
| `--vector-store DIR` | Optional. Reuses a prebuilt **ai-safety** store instead of re-embedding. The corpus consistency check still runs. |
| `--science-projects` (or `VISTA_BACKEND_SEED_SCIENCE_PROJECTS=true`) | Also requires and packs `molten-salt-papers/` and `mstdb/`, and copies the forge-tune CSV. |
| `--science-projects-vector-store DIR` | Optional, and only valid with `--science-projects`. Reuses a prebuilt molten-salt store. |
| `--payload DIR` | Unchanged meaning: one unpacked vista-data tree. Only the selected folders are copied from it. |

`build_vector_store` and `check_store_matches_corpus` take a slug, and are called once for
`ai-safety` and, with science enabled, once for `molten-salt-papers`. `--without-citations`
applies to both. `--help` and the README describe both store options as **optional, only
for reusing an existing prebuilt store to skip re-embedding**. Omitting them always
produces a correct build.

### D6. Verification

`smoke_test_package.sh` queries `rag_search` through
`/projects/ai-safety-autonomous-labs/mcp/call` with `kb_slug: ai-safety` and an
agent-security query (e.g. "memory poisoning in LLM agents"). For a default build it also
asserts that the unpacked package and its first-run state contain no
`vista-data/mstdb`, no `vista-data/molten-salt-papers`, no
`knowledge-bases/molten-salt-papers` and no `hpc_jobs/forge-tune/*.csv`. The build
recording whether science was enabled lets the check know which case applies.
`ui/e2e/smoke.spec.ts` looks for the `ai-safety-autonomous-labs` card.

### D7. Project content

The description reads as a one-line "AI Safety in Autonomous Labs" summary.
`system_prompts/ai-safety-autonomous-labs.md` frames:
- the corpus: the interconnected-autonomous-labs roadmap, autonomy-induced security risks
  in LLM agents, and autonomous-driving security and privacy as a cyber-physical analogue
  whose findings transfer to lab automation;
- a preference for citing `ai-safety` passages;
- encouragement to discover and run the available HPC jobs with its tools, naming none.

The draft is written during implementation and reviewed by Sam.

## Risks / Trade-offs

- [Deleting `ai-safety-autonomous-labs` brings it back on the next boot] → Accepted, since
  it's the same semantics as `sync_bundled_skills`. It's documented in
  `project-onboarding.md` and covered by a spec scenario.
- [A dev boot with a token pays one indexing run, and hits GitLab while the KB is missing] →
  One-time, about a minute. Failure only warns (D3).
- [Existing packaged installs keep MSTDB and molten-salt data on disk] → Accepted (legacy
  data is left alone). "Not distributed" applies to new artifacts, which D6 verifies.
- [`forge-tune` is listed but cannot run without its CSV in default installs] → Accepted.
  The job remains for science builds, and per-project job scoping is a non-goal.
- [Hermetic tests must not reach GitLab] → The sync tests use `LocalRepoClient` payloads in
  `tmp_path` and the no-client path. The token path is covered by faking `has`/`download_*`.

## Migration Plan

No schema migration: new rows only. Existing dev DBs and packaged installs receive the new
project on their next boot (D1, D4). Rollback means reverting the change; the inserted
project and KB rows are inert for older code (the KB is still discovered by MCP).
