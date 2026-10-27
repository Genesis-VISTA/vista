## Why

VISTA's docs live in two places: `docs/` in this repo, and genesis-vista.github.io, whose
pages are copied by hand. The site's Quick Start already repeats `docs/installing.md`, and
the copies drift. Nothing tells a doc author whether their page is public, and nothing checks
a page the way the site will render it.

## What Changes

- A manifest, `docs/site.toml`, lists the `docs/*.md` pages the site publishes, in nav order,
  with their titles and sections (Reference, Developers), and lists the rest as excluded.
  Adding a page to the site, or keeping one off it, is an edit in the same MR as the doc.
- The sync script moves from the site repo into `scripts/docs_site/`. It copies the listed
  pages into the site's tree, rewrites links into the source tree to the release's files on
  GitHub, re-indents GitLab-style nested lists for the site's Markdown, adds a note saying
  where to edit the page, and writes the site's nav from the manifest. A release therefore
  carries the code that publishes it.
- The `docs` CI target gains two jobs next to `docs:links`:
  - `docs:manifest` (lint, stdlib only, also in the pre-commit hook): every `docs/*.md` is
    listed or excluded, never both, and every listed file exists.
  - `docs:site` (test): builds the published pages with MkDocs in strict mode against a
    minimal copy of the site's config, so a broken anchor or a manifest entry that doesn't
    resolve fails the MR, not the site's build after a release.
- The site (separate repo, separate PR) publishes the docs of VISTA's **latest release**,
  never a pre-release or `main`, by running that release's sync script before its build, and
  rebuilds daily to pick up a new release.

## Non-goals

- No publishing from `main` or pre-releases, and no "dev" docs build.
- The excluded docs (releasing, validation lane, evaluation runbook, PALISADE, the backend
  plan) stay repo-only; changing that is a manifest edit, not part of this change.
- The site's tutorials, theme and landing page stay in the site repo.
- No deploy from GitLab CI and no release-time trigger of the site's workflow; a daily run
  is enough.
- No VISTAGuard involvement.

## Capabilities

### New Capabilities
- `docs-site`: which docs the public site publishes, how a page is checked before it can
  merge, and which release the site shows.

### Modified Capabilities
None. `testing-ci`'s local mirror already covers new jobs in an existing target.

## Impact

- New: `docs/site.toml`, `scripts/docs_site/` (sync script, its tests, the minimal MkDocs
  config).
- Changed: `scripts/ci-local.sh` (`docs` target), `.gitlab-ci.yml` (`docs:manifest`,
  `docs:site`), `AGENTS.md` (the manifest rule; `docs` in the target list).
- CI gains a test job that installs `mkdocs-material` from PyPI at a pinned version. It uses
  no credentials, so PR CI stays hermetic.
- genesis-vista.github.io: a PR replacing its copy of the sync with this one. It can only go
  live after a VISTA release that includes this change; until then its PR build fails,
  because v0.1.4 has no manifest.
