## 1. OLCF docs converter (D1–D3)

- [ ] 1.1 Write the pure split function in `scripts/olcf_docs/convert.py`: page HTML in, section files out (named per D2, header per D2, long sections split per D3, refusal of a path holding `__`). Verify with `scripts/olcf_docs/test_convert.py`. Its fixture HTML holds a current page with two sections, a legacy page with a top-of-page decommission admonition, a section over 2,000 words with subsections, two `index` pages in different directories, a `sphinx_design` tab set, a table and a code block. Assert the file names, header URLs and notices, and that no two names collide.
- [ ] 1.2 Add the command line `uv run scripts/olcf_docs/convert.py <docs-checkout> <out-dir>`. It runs `sphinx-build -b html` with the checkout's own requirements in a throwaway environment, applies 1.1 to every page, and writes `SOURCE` with the checkout's commit. Verify against a real checkout of `olcf/olcf-user-docs`: every page yields at least one file, `SOURCE` holds the commit, and three header URLs (Frontier "Running Jobs", Odo's intro, a Summit section) open the right section on docs.olcf.ornl.gov. This is a manual step that needs the network, and it stays out of PR CI.
- [ ] 1.3 Add `scripts/olcf_docs/check_retrieval.py <rag_db>` and `questions.toml`, about 20 questions each naming its expected file prefix in the top 5 (D8). Verify that it exits non-zero on a deliberately wrong expectation.
- [ ] 1.4 Run the `scripts/olcf_docs` tests from the `docs` target of `./scripts/ci-local.sh` and in the matching GitLab job, the same way `scripts/docs_site` tests run. Verify that `./scripts/ci-local.sh docs` runs and passes them.

## 2. Corpus refresh (Sam, manual, outside PR CI)

- [ ] 2.1 Convert `olcf/olcf-user-docs` at a chosen commit (1.2), index the output into a store with citations on, and run `check_retrieval.py` against it. Verify that all questions pass, and record the file count and store size in design.md's Open Questions.
- [ ] 2.2 Commit `olcf-docs/` (Markdown, `SOURCE`, `rag_db/`) to vista-data. In `vista-build-inputs`, replace `vista-data/ai-safety` and `rag_db` with `vista-data/olcf-docs` and the new store, then bump `BUILD_INPUTS_COMMIT` in `.github/build-inputs.env`. Verify with `./scripts/build_local_package.sh --check --payload <inputs>/vista-data --vector-store <inputs>/rag_db`.

## 3. Job skeleton and hello skills (D6)

- [ ] 3.1 Check that Odo and Frontier jobs see `RUN_DIR_Odo` / `RUN_DIR_Frontier`, and read `validate.py`'s rules on `cluster_defaults.json` entries against job scripts. Record anything that changes D6 in design.md before going on.
- [ ] 3.2 Add `job.odo.slurm` and `job.frontier.slurm` to `hpc-skill-author/templates/job/`, add `odo` and `frontier` to its `cluster_defaults.json`, and update its README and SKILL.md: delete the untargeted clusters, use the `olcf-docs` KB for software environments. Verify with a test that fills the template per cluster and runs `validate.py` on each, passing.
- [ ] 3.3 Write `db/skills/hello-{odo,frontier,lux}/`, each with `SKILL.md` and `hpc_jobs/hello/` (that cluster's script and defaults only, `input.txt`, README) as the filled-in skeleton (D6). Verify with a test that `validate.py` passes on all three.
- [ ] 3.4 Add a dry-run test in `mcp_servers/vista_mcp_server/tests/` that submits `hello-frontier/hello`. Verify that `input.txt` is in the upload, the job is accepted, and a missing Lux account refuses `hello-lux/hello` with the settings message.
- [ ] 3.5 Remove `hpc_jobs/lux-hello`, and `hpc_jobs/example` unless a grep finds other references. Verify that the MCP tests pass and that `AVAILABLE_JOBS` lists neither.
- [ ] 3.6 Live: run each hello skill from the `hello-olcf` project on Odo, Frontier and Lux. Verify that each report shows the node, GPUs, modules and the input checksum. This is a manual step by Sam, outside PR CI.

## 4. Default project seeding (D4, D5)

- [ ] 4.1 Write `db/system_prompts/hello-olcf.md`: cite OLCF passages with their URL, `fetch_kb_file` when a hit lacks one, offer a hello run on a connected cluster, offer to author a job. Remove `ai-safety-autonomous-labs.md`. Verify that Sam reviews the wording.
- [ ] 4.2 Replace the AI-safety seeding in `db/seed.py` with hello-olcf: new constants, slug-parameterised corpus acquisition that installs a fetched `rag_db/` instead of indexing (D4), and the project's four skills. Keep the AI-safety ids in `_vista_ships()`. Verify that 4.3's tests pass.
- [ ] 4.3 Rewrite `backend/tests/test_default_projects_sync.py` and update `test_seed_payload.py` and `test_incompatible_database.py`. Verify these cases pass under the hermetic filter:
  - a fresh DB has `hello-olcf` with `olcf-docs` and the four skills, and no AI-safety project;
  - a DB holding the AI-safety project keeps it unchanged and gains `hello-olcf`;
  - a deleted AI-safety project is not recreated;
  - a deleted `hello-olcf` is;
  - a token-fetched folder with `rag_db/` is installed without calling the indexer;
  - a payload with only `olcf-docs/` seeds;
  - the skills resolve although they are synced after the project (D5).

## 5. Packaging (D9)

- [ ] 5.1 Make `stage_payload`, `PAYLOAD_MEMBERS`, the manifest counts, `build_vector_store` and `check_store_matches_corpus` in `scripts/build_local_package.sh` use `olcf-docs` and every suffix the indexer accepts. Verify that `--check` fails naming `olcf-docs` when it is missing, and fails on a reused store that cites a Markdown file absent from the payload.
- [ ] 5.2 Point `scripts/smoke_test_package.sh`'s retrieval check at `hello-olcf` / `olcf-docs` with a Frontier GPU question. Verify that a full local default build passes its smoke test.

## 6. Onboarding tour (D7)

- [ ] 6.1 Add `ui/lib/default-project.ts` (project name, KB slug), use it in `ui/app/projects/page.tsx` and `ui/app/knowledge-bases/explorer.tsx`, and add a backend test that it matches the seed constants. Verify that the test fails when either side is renamed.
- [ ] 6.2 Rewrite `ui/lib/onboarding-copy.ts`: project card and open, the KB steps ("OLCF User Documentation", "its documents"), the skills step naming the skill it points at, the starter question from the onboarding-tour delta, and the first-question wording. Verify with `ui/tests/onboarding.test.ts`.
- [ ] 6.3 Replace `ui/lib/onboarding-examples.ts` with a hello run's outputs: a markdown report comparing the three clusters, a chart image and the CSV input, keeping the step's shape. Update the copy naming them. Verify with `ui/e2e-hermetic/onboarding.spec.ts`.
- [ ] 6.4 Update `ui/e2e/smoke.spec.ts` to look for the `hello-olcf` card. Verify with `cd ui && npm run lint`.

## 7. Docs and verification

- [ ] 7.1 Update `docs/project-onboarding.md`, `docs/building-packages.md`, `docs/validation-lane.md` and `docs/releasing.md`. In `releasing.md`, rewrite "Updating them" around the converter and `check_retrieval.py`. Verify that `./scripts/ci-local.sh docs` passes.
- [ ] 7.2 Run `./scripts/ci-local.sh` and `openspec validate hello-olcf-default-project --strict`. Verify that both pass.
- [ ] 7.3 Live: on a fresh state directory, take the tour through the starter question. Verify that the answer cites `olcf-docs` passages with docs.olcf.ornl.gov URLs. This is a manual step by Sam, outside PR CI.
