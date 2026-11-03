## Why

VISTA's out-of-the-box project, AI Safety in Autonomous Labs, shows literature search over
three papers and nothing of what VISTA is for: running work on OLCF machines. A new
researcher should instead open a project that answers their OLCF questions from the
facility's own documentation, runs a first job on each OLCF cluster VISTA supports, and
helps them write their own. The pieces mostly exist (`hpc-skill-author`, hello jobs for
Odo, Frontier and Lux), but none of them is wired into the first thing a researcher sees.

## What Changes

- New default project `hello-olcf` (fixed UUID), seeded on every install in place of
  `ai-safety-autonomous-labs`. It loads `hello-odo`, `hello-frontier`, `hello-lux` and
  `hpc-skill-author`, attaches the `olcf-docs` knowledge base, keeps the tools glob
  `["*", "!agenthpc_*"]` and a request limit of 50, and has its own system prompt: cite the
  OLCF docs with their URLs, offer a hello run on a connected cluster, then offer to author
  a job.
- New knowledge base `olcf-docs` ("OLCF User Documentation"): the whole
  `olcf/olcf-user-docs` repository at a recorded commit, converted to Markdown with one file
  per top-level section. Each file is named from its path and starts with a header that
  gives the page and section title, its docs.olcf.ornl.gov URL, and the page's own archive
  or decommission notice when it has one. The indexer and `rag_search` are unchanged.
- New converter `scripts/olcf_docs/convert.py`. It runs only when a maintainer refreshes the
  corpus, never on a researcher's machine. Refreshing is manual; this change does it once.
- New bundled skills `hello-odo`, `hello-frontier` and `hello-lux`. Each ships one job that
  does a round trip: it uploads a small input, reports the node, GPUs and loaded modules,
  writes an output and fetches it back. Each job is the `hpc-skill-author` job skeleton
  with its work filled in.
- `hpc-skill-author`'s job skeleton gains Odo and Frontier (`job.odo.slurm`,
  `job.frontier.slurm` and their `cluster_defaults.json` entries) beside Lux. Its
  software-environment guidance points at the `olcf-docs` knowledge base.
- **BREAKING (defaults)**: new installs no longer get `ai-safety-autonomous-labs` or the
  `ai-safety` knowledge base. Existing installs keep their copies, and they are no longer
  restored if deleted. The AI-safety seed code, system prompt and tests are removed. Its
  ids stay in the list of what VISTA shipped, for the incompatible-database index.
- **BREAKING (packaging)**: the package bundles the `olcf-docs` corpus and its prebuilt
  index instead of `ai-safety`. The build indexes Markdown corpora as well as PDFs.
  `vista-build-inputs` and its pin in `.github/build-inputs.env` carry `olcf-docs`.
- The onboarding tour points at `hello-olcf` and the `olcf-docs` knowledge base. Its starter
  question becomes a question about the OLCF docs, its example files become a hello run's
  outputs, and its project and knowledge-base lookups use ids shared with the seed.
- `hpc_jobs/lux-hello` is removed (superseded by `hello-lux`). `hpc_jobs/example` is
  removed if nothing else references it.

Out of scope: Perlmutter (no access to verify it); a license for redistributing the OLCF
docs (asked for separately, and not a gate); keeping installed copies of the docs current
(later, through the collaboration server); scheduled live validation.

## Capabilities

### New Capabilities
- `olcf-docs-corpus`: how the OLCF user docs become a knowledge-base corpus. Covers the
  whole repository at a recorded commit, section files named from their path, the header
  with title, URL and status notice, and conversion only at refresh time.
- `hello-cluster-skills`: the bundled `hello-<cluster>` skills for Odo, Frontier and Lux.
  Covers the round-trip job, checking that the cluster is ready before submitting, and
  reporting each cluster's likely failure plainly.

### Modified Capabilities
- `default-projects`: the default project becomes `hello-olcf` with the `olcf-docs`
  knowledge base and four skills. Payload seeding and the additive sync follow it. The
  AI-safety project is no longer seeded or restored, and existing copies are left alone.
- `laptop-distribution`: the bundled, pre-indexed corpus becomes `olcf-docs`. Build-time
  verification queries it. An upgraded install receives it beside an existing `ai-safety`
  corpus.
- `release-builds`: the pinned build inputs carry the `olcf-docs` corpus and its store, and
  record the docs commit they were built from.
- `onboarding-tour`: the project card, the knowledge-base step and the starter question
  name `hello-olcf` and the OLCF docs.
- `hpc-skill-authoring`: the job templates cover Odo and Frontier as well as Lux.

## Impact

- **Backend**: `db/seed.py` (hello-olcf project and KB, slug-parameterised corpus
  acquisition, AI-safety seeding removed), `db/db.py` (`_vista_ships`), new
  `db/system_prompts/hello-olcf.md`, removed `ai-safety-autonomous-labs.md`, new
  `db/skills/hello-{odo,frontier,lux}/`, `db/skills/hpc-skill-author/templates/job/`.
- **Corpus tooling**: new `scripts/olcf_docs/` (converter and its tests), `ci-local.sh`.
- **Packaging**: `scripts/build_local_package.sh` (corpus slug, Markdown documents),
  `scripts/smoke_test_package.sh`, `.github/scripts/package.sh` if its checks name the
  corpus, `.github/build-inputs.env`.
- **UI**: `ui/lib/onboarding-copy.ts`, `ui/lib/onboarding-examples.ts`,
  `ui/app/projects/page.tsx`, `ui/app/knowledge-bases/explorer.tsx`, and their unit and
  e2e tests.
- **Tests**: `test_default_projects_sync.py`, `test_seed_payload.py`,
  `test_incompatible_database.py` (the v0.1.4 fixture stays).
- **Docs**: `docs/project-onboarding.md`, `docs/building-packages.md`, `docs/releasing.md`
  (build inputs and refresh), `docs/validation-lane.md`.
- **Data**: `olcf-docs/` in vista-data (for dev seeding) and in `vista-build-inputs` (corpus
  and `rag_db`), committed by a maintainer. No MCP server change.
