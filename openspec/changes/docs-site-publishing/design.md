## Context

genesis-vista.github.io is a separate public GitHub repo, maintained through PRs by the
tutorial authors. It builds with MkDocs Material (`>=9,<10`, 9.8.0 today) from
`documentation/` and deploys with GitHub Pages from its own `pages.yml` workflow. This repo is
canonical on GitLab and mirrored, publicly, to `Genesis-VISTA/vista`, tags included; the
mirror overwrites GitHub-only commits.

A spike, and then a draft PR on the site repo (local, unpushed), proved the sync with the
site's own config: it copied 12 pages from v0.1.4 and passed `mkdocs build --strict` with
anchor validation. The strict build caught 8 links the first rewriter missed, and an A/B
build showed that a site-wide list extension breaks the site's tutorials, which nest with 4
spaces. This change moves that script here and makes `docs/` its input.

`docs:links` (`scripts/check_md_links.py`) already checks every relative link and anchor in
the tracked Markdown, using GitHub's slug rules, offline and stdlib-only.

## Goals / Non-Goals

**Goals:**
- One place decides what is published: `docs/site.toml`, edited in the MR that adds or renames
  a doc.
- The site needs no change when a doc is added, renamed or retitled, or when the publishing
  step itself changes.
- A page that would break the site's build fails the MR.

**Non-Goals:**
- Validating the site's tutorials or theme from this repo.
- Checking external URLs (as `docs:links`, offline by design).

## Decisions

**TOML manifest, read with `tomllib`.** `docs:manifest` runs in lint and in the pre-commit
hook, next to the stdlib-only `docs:links`. TOML needs nothing installed; YAML would need
PyYAML in every lint environment. The site's workflow runs Python 3.x, which has `tomllib`.
`tomllib` needs Python 3.11, and a Mac's system `python3` can be older (3.9 here), so
`ci-local.sh` runs the check as `uv run --no-project --python 3.14 python -I`, which it
already requires `uv` for; CI's image runs it with its own `python3` (3.14). Either way it
installs nothing.

```toml
# Which docs/ pages genesis-vista.github.io publishes, in nav order.
exclude = ["releasing.md", "validation-lane.md", "evaluation-runbook.md", "palisade.md", "new_backend_plan.md"]

[[section]]
title = "Reference"
pages = [
  { file = "installing.md", title = "Installing VISTA" },
  ...
]

[[section]]
title = "Developers"
pages = [ ... ]
```

**The sync script lives here and the site runs the release's copy.** The site's workflow
checks out the release with `docs/` and `scripts/docs_site/` (sparse) and runs
`scripts/docs_site/sync_docs.py <vista> <site> --ref <tag>`. That command line is the only
contract with the site repo, and keeping it stable is this repo's job. Alternatives: keeping
the script in the site (vista CI couldn't run it without fetching another repo, and a format
change would need coordinated PRs); a published package (more machinery than one script).

**The script writes the site's nav, into a generated config.** MkDocs can't include part of
a nav from another file, and `INHERIT` replaces lists rather than merging them. The script
reads the site's nav from its `mkdocs.yml`, appends the manifest's sections after it, and
writes `mkdocs.generated.yml` next to it (so relative paths resolve the same) holding only
`INHERIT: mkdocs.yml` and that nav. MkDocs merges every other key from the site's config, so
the script never re-writes it, and YAML tags in it (`!!python/name`, common in Material
configs) survive. The site builds
with `-f mkdocs.generated.yml`; both the generated config and the copied pages are
gitignored there. All copied pages go under `documentation/reference/`, whatever their
section, so URLs don't change when a page moves between sections.

**The strict build in CI uses a minimal site config here**: `scripts/docs_site/mkdocs.yml`,
with the site's theme and Markdown extensions, its anchor validation, and an empty nav, built
into a temporary directory. It doesn't fetch the site repo, so the job stays hermetic and is
unaffected by the site's tutorials. `mkdocs-material` is pinned at the version the site
resolves, run through `uvx` as ruff is.

**What each check owns.** `docs:links` keeps checking that source-tree links exist (it sees
the whole repo; the site's sparse checkout can't). `docs:manifest` owns coverage. `docs:site`
owns how pages render: anchors as MkDocs slugs them, nav entries that resolve, and links the
rewriter mishandles. The sync script itself checks nothing beyond failing on a listed file
that is missing.

**Lists are re-indented per page by the script**, not by a site-wide extension, because the
site's tutorials already use 4-space nesting (the A/B result above). It changes whitespace
only. A wrapped line that starts with `+ ` or `- ` becomes a list item, as it does on
GitLab.

## Risks / Trade-offs

- [The minimal config drifts from the site's extensions] → A comment in both configs names
  the other. `docs:site` only has to be at least as strict as the site.
- [The site's PR build fails until a release carries the manifest] → Expected and stated in
  that PR; it is merged after the release.
- [The site's maintainers no longer arrange the Reference and Developers sections] → Agreed
  with them before the site PR leaves draft; their own nav still comes first.
- [Material for MkDocs reaches end of life on 2027-05-05] → Nothing here depends on
  Material-only features beyond the theme, so a move to its successor changes two configs.
- [Changing the script's command line breaks the site at the next release] → It is stated in
  `scripts/docs_site/sync_docs.py` as a contract; a change ships with a site PR.

## Migration Plan

1. Merge this change. `main` gains the manifest, the script and the checks; the site is
   unaffected.
2. Publish the next release.
3. Mark the site PR ready and merge it; its next build publishes that release's docs.

Rollback: revert the site PR, which restores the site's hand-written nav. Nothing in this
repo needs reverting.
