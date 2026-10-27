## 1. Manifest and coverage check

- [x] 1.1 Add `docs/site.toml`: Reference (installing, hpc, hpc-jobs, skill-onboarding,
  project-onboarding, api-example-alloy-tc, multi-agent-framework, splash-planner-playbook,
  hypothesis-forum-hosting, forum-git-format) and Developers (development, building-packages),
  with the titles from the site draft; exclude releasing, validation-lane,
  evaluation-runbook, palisade, new_backend_plan. Verify every `docs/*.md` appears once.
- [x] 1.2 Add `scripts/docs_site/check_manifest.py` (stdlib only): fails, naming the files,
  on a doc neither listed nor excluded, a listed file that is missing, or a file both listed
  and excluded. Verify with tests in `scripts/docs_site/test_check_manifest.py` covering the
  three `docs:manifest` scenarios in the spec, and `python3 -I scripts/docs_site/check_manifest.py`
  passing on this tree.
- [x] 1.3 Add `docs:manifest` to the `docs` lint in `scripts/ci-local.sh` and to
  `.gitlab-ci.yml` (lint stage, beside `docs:links`). Verify `./scripts/ci-local.sh docs lint`
  passes, and fails after adding an unlisted `docs/scratch.md`.

## 2. Sync script

- [x] 2.1 Move `sync_docs.py` and its tests from the site draft into `scripts/docs_site/`:
  read the manifest instead of the site's nav, keep link rewriting, list re-indenting and the
  source note, and state the command line (`<vista> <site> --ref <tag>`) as the contract with
  the site. Verify the moved tests pass.
- [x] 2.2 Write the site's nav: read the site's `mkdocs.yml`, append the manifest's sections
  after its own nav, and write `mkdocs.generated.yml` beside it; copied pages go under
  `documentation/reference/`. Verify with a test that a site nav of two entries plus a
  two-section manifest gives four top-level entries in order, and that the site's other
  config keys survive.

## 3. Strict build in CI

- [x] 3.1 Add `scripts/docs_site/mkdocs.yml`: the site's theme and Markdown extensions, anchor
  validation, empty nav, and a comment naming the site's `mkdocs.yml` (and the reverse
  comment goes in the site PR). Verify the generated config builds.
- [x] 3.2 Add `docs:site` to the `docs` test action in `scripts/ci-local.sh` and to
  `.gitlab-ci.yml` (test stage): run the script's tests, sync this tree into a temporary site
  built from the minimal config, and `mkdocs build --strict` with `mkdocs-material==9.8.0`,
  in one `uv run --with` environment, through one script (`scripts/docs_site/check_site.sh`)
  that both run, without credentials. Verify `./scripts/ci-local.sh docs test` passes, and
  fails after breaking an anchor in a published page.

## 4. Docs

- [x] 4.1 `AGENTS.md`: a new doc must be listed or excluded in `docs/site.toml`; the site
  shows the latest release; add `docs` to the `ci-local.sh` target list. Verify
  `./scripts/ci-local.sh docs` passes.
- [x] 4.2 Note in `docs/development.md` (or wherever the docs are introduced) that the
  published pages are on genesis-vista.github.io and how to preview them locally. Verify the
  preview command works from a clean checkout.

## 5. Site PR (genesis-vista.github.io, outside this repo)

- [x] 5.1 Rework the site draft: remove its own sync script and the Reference and Developers
  nav entries; in `pages.yml`, sparse-check out the latest release's `docs/` and
  `scripts/docs_site/`, run its `sync_docs.py`, and build with `-f mkdocs.generated.yml`;
  gitignore the generated config. Keep the link-colour fix and the ModSim tutorial links.
  Verify locally against this branch's checkout: the strict build passes and the pages match
  the draft's.
- [x] 5.2 Open this repo's MR and the site PR as drafts, each linking to the other. The site
  PR states that its build fails until a release carries the manifest.

## 6. Go live (after merge)

- [ ] 6.1 After the next release, mark the site PR ready, and confirm with its maintainers
  before merging. Verify the deployed site shows that release in each page's note.
