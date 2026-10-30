## Context

See proposal.md for why. The current state that shapes the approach:

- **Default project.** `sync_default_projects` (`backend/src/vista_backend/db/seed.py:585`)
  runs on every startup and only adds rows. It inserts the AI-safety project and KB by fixed
  id and recreates them if deleted. `_vista_ships()` (`db/db.py:233`) lists the shipped ids
  for the incompatible-database index.
- **Corpus acquisition.** `_acquire_ai_safety_corpus` (`seed.py:542`) copies vista-data's
  `ai-safety/` into `knowledge_bases/<slug>/pdfs`. From a vista-data token it then indexes
  locally. From a payload it expects the store already built.
- **Indexing.** `build_rag.py` accepts `.md`. Each Markdown file gets 512-word chunks and one
  LLM description written from its first 2,000 words. Hits are keyed by file basename, and
  `rag_search` prints that description and a `fetch_kb_file(...)` hint on every hit from the
  file. There is no `.rst` support and no URL field.
- **Packaging.** `build_vector_store` and `check_store_matches_corpus`
  (`scripts/build_local_package.sh:1295-1424`) glob only `*.pdf`. CI always reuses the store
  from `vista-build-inputs` (`.github/scripts/package.sh:77-80`).
- **OLCF docs.** `olcf/olcf-user-docs` is Sphinx RST: 144 pages, about 2.4 MB, using
  `sphinx_design` and `sphinx_copybutton`. Legacy pages carry their own "archive /
  decommissioned" notice at the top. Odo and Lux have pages.
- **Jobs.** `submit_hpc_job` already refuses with a `ToolError` that names a missing
  setting (for example `require_lux_account`, `lib/user_config.py:131`). Accounts come from
  the Lux account setting and from S3M introspection. `hpc-skill-author`'s `templates/job/`
  is Lux-only and separate from `hpc_jobs/lux-hello`.

## Goals / Non-Goals

**Goals:**
- Every OLCF citation can be traced to its exact docs.olcf.ornl.gov section, using only
  data the converter writes.
- No indexing or retrieval behaviour changes for any knowledge base.
- Each hello job is checked by the same validator as an agent-written skill.

**Non-Goals:**
- Heading-aware chunking, structured citation fields, or a URL column in the index.
- A readiness tool for the agent. The submit tool's own refusal is the signal.
- Automating the corpus refresh.

## Decisions

### 1. Convert from Sphinx's own HTML, not from the RST source

`scripts/olcf_docs/convert.py <docs-checkout> <out-dir>` builds the checkout with
`sphinx-build -b html`, using the repo's own `conf.py` and Sphinx extensions. It then splits
each page's HTML at its top-level `<section>` elements and converts each section to
Markdown (markdownify). The script declares its dependencies inline (PEP 723) and runs with
`uv run`, so nothing is added to any project's dependencies.

- **Why:** Sphinx resolves includes, `:ref:` roles, `sphinx_design` tabs and grids, and
  substitutions. Its section `id`s are exactly the anchors docs.olcf.ornl.gov serves, so a
  header's URL is correct by construction.
- **Alternatives:**
  - pandoc on the RST: it doesn't resolve Sphinx directives or roles, and we would have to
    guess the anchors.
  - The third-party `sphinx-markdown-builder`: anchors aren't guaranteed, and it's one more
    dependency to trust.
  - A `.rst` extractor in `build_rag.py`: it would chunk raw markup and change the indexer,
    which this change rules out.

The split-and-write step is a pure function from a page's HTML to its section files. That
is what the unit tests exercise, with HTML fixtures and no Sphinx.

### 2. File names carry the path; headers carry the URL

- **Names.**
  - A section file is named `<page path with / as __>__<section id>.md`, for example
    `systems__frontier_user_guide__running-jobs.md`.
  - The content before the first section becomes `<page path with / as __>.md`.
  - The converter refuses a source path that already contains `__`, so names cannot
    collide. This also fixes the collisions between the repo's many `index.rst` files under
    the basename keying.
- **Headers.** Each file starts with this header, written by the converter:

  ```markdown
  # Frontier User Guide › Running Jobs

  Source: https://docs.olcf.ornl.gov/systems/frontier_user_guide.html#running-jobs

  > Notice: <each admonition at the top of the page, before its first section, as text>
  ```

- **Why the header is enough for the URL:** the LLM description is written from the file's
  first 2,000 words, so it always sees the header and states the status. The system prompt
  tells the agent to `fetch_kb_file` a cited file whose hit doesn't show a URL. Section
  files are small, so that fetch is cheap.
- **Alternatives:**
  - A `sources.json` sidecar read by the indexer and `rag_search`: exact, but it changes
    indexing.
  - A URL rule written into the system prompt: works, but the agent would build URLs rather
    than quote them.

### 3. Long sections split once more, at subsections

A top-level section over 2,000 words is written as one file per subsection, and any text
before its first subsection becomes a file named for the section itself. 2,000 words is
the description window. Below it, the description covers the whole file. Deeper nesting is
not split further. The Frontier guide's "Running Jobs" section is the main case.

### 4. Where the corpus lives

- **vista-data.** `olcf-docs/` holds the converted Markdown, a `SOURCE` file (no suffix, so
  the indexer skips it) recording the docs commit, and the prebuilt `rag_db/`. It is used
  for dev seeding through the existing token path.
- **vista-build-inputs.** `vista-data/olcf-docs/` and `rag_db/` replace `ai-safety`, as
  before.
- **Prebuilt store from a token.** Corpus acquisition takes the slug as a parameter. When
  the fetched folder holds a `rag_db/`, it installs that store instead of indexing.
- **Why:** without the prebuilt store, a dev checkout's first boot would make one LLM call
  per section file (on the order of a thousand) before the project's KB is usable.
- **Alternative:** dev seeding indexes locally, as it does for AI safety today. It is
  simpler, but turns a few-second first boot into minutes and costs a model key per
  developer.

### 5. Seeding

- **New constants.** `HELLO_OLCF_PROJECT_ID`, `OLCF_DOCS_KB_ID`, `OLCF_DOCS_SLUG` and
  `HELLO_OLCF_PROJECT_NAME`.
- **The sync body** seeds hello-olcf where it seeded AI safety. Its `skills` field is
  `["hello-odo", "hello-frontier", "hello-lux", "hpc-skill-author"]`. The tools glob and the
  request limit of 50 are unchanged.
- **AI safety.** The AI-safety constants stay only for `_vista_ships()`, and everything else
  of AI safety is removed. Because the sync only adds rows, existing AI-safety rows are
  untouched and never restored, with no new branch.
- **Ordering.** `sync_bundled_skills` runs after `sync_default_projects` (`db/db.py:376-378`).
  The project stores skill names, so it must not require the skills to exist when it is
  inserted. A task verifies this against how skills are resolved at agent build time.

### 6. Hello skills are the job skeleton, filled in

- **Skeleton.** `hpc-skill-author/templates/job/` gains `job.odo.slurm` and
  `job.frontier.slurm`, and its `cluster_defaults.json` gains `odo` and `frontier` entries
  beside `lux`. Its README tells the agent to delete the clusters a skill doesn't target.
- **Hello skills.** Each `db/skills/hello-<cluster>/` ships `hpc_jobs/hello/` holding:
  - that cluster's script and defaults only;
  - `input.txt`;
  - a README.
- **The job.** It writes `hello.txt` with the host and node list, `rocm-smi` (all three
  clusters are AMD), `module list`, start and end times, and the input's contents and
  `sha256sum` from `RUN_DIR_<Cluster>`. It also writes the skeleton's `run.log` and
  `summary.txt`.
- **SKILL.md** covers the loop: submit, follow the log, fetch `hello.txt`, show it. It names
  the cluster's likely failure: an expired Globus session (Odo, Frontier), the RSA passcode
  prompt or a missing Lux account (Lux). It tells the agent not to retry until the
  researcher says the connection is made.
- **Why fill the skeleton:** a live hello run is then evidence that the template an
  authored skill starts from actually runs on that cluster.
- **Tests:** the validator runs over all three hello skills and over the template filled in
  per cluster.

### 7. The tour finds the project by shared constants

The UI can't import Python, so `ui/lib/default-project.ts` exports the project name
`hello-olcf` and the KB slug `olcf-docs`. `projects/page.tsx` and `explorer.tsx` use them.
A backend test reads that file and asserts it matches the seed constants. A rename then
fails CI instead of silently breaking the tour.

### 8. Measurable retrieval gate

`scripts/olcf_docs/check_retrieval.py <rag_db>` runs about 20 questions from
`questions.toml`, each naming the file prefix expected in the top 5 results. A maintainer
runs it after each refresh. It is a gate on the refresh, not on PR CI, because the store
isn't in the repo.

### 9. Packaging

- `stage_payload` stages `olcf-docs` instead of `ai-safety`.
- `build_vector_store` and `check_store_matches_corpus` take every suffix the indexer
  accepts, not only `*.pdf`. The manifest counts documents, not PDFs.
- `smoke_test_package.sh` queries `hello-olcf` / `olcf-docs` with a Frontier GPU question.
- `.github/scripts/package.sh` keeps its flags.

## Risks / Trade-offs

- [The Sphinx build of `olcf-user-docs` needs a network, has unpinned requirements, or
  warns] → The converter installs the docs repo's own requirements into a throwaway `uv`
  environment and runs it at refresh time on a maintainer's machine, never in CI. Warnings
  are reported, not fatal.
- [markdownify garbles `sphinx_design` tabs or wide tables] → Fixtures cover a tab set, a
  table and a code block. The Frontier and Lux guides are spot-checked after the refresh.
- [The agent quotes a passage without fetching its URL] → The system prompt requires a URL
  on every OLCF citation. The live check of the tour's starter question looks for it.
- [About a thousand small files make the KB's document list long in the UI] → It is
  accepted. The tour points at the description, not at a specific document.
- [vista-data grows by a prebuilt store] → It is a one-time commit of tens of MB, replaced
  only on a refresh.
- [Docs redistribution permission] → Sam is asking OLCF separately. Not a gate.

## Migration Plan

1. Land the code with the corpus available in vista-data and `vista-build-inputs`: Sam
   commits the converted corpus and store, then bumps `.github/build-inputs.env`.
2. Existing installs, on upgrade:
   - The sync adds `hello-olcf` and `olcf-docs`, and AI safety stays.
   - A package installs the new corpus next to `ai-safety` (`parts.txt`, unchanged
     mechanism).
3. Rollback is the previous package or commit. The added rows are ordinary projects and KBs
   that the old version ignores. Its own sync re-adds AI safety where it is missing.

## Open Questions

- The section-file count and total store size after the first real conversion. It only
  affects the numbers in the docs.
- Whether `hpc_jobs/example` has references beyond the hello case. Removal follows what a
  grep finds.
