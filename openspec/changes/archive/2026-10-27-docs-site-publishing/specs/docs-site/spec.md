## Purpose

Decides which of VISTA's docs the public site, genesis-vista.github.io, publishes, checks
those pages before they can merge, and ties what the site shows to VISTA's latest release.

## ADDED Requirements

### Requirement: The manifest decides what the site publishes
`docs/site.toml` SHALL list the pages the site publishes, grouped into named sections, in the
order the site shows them, each with the title it has in the site's navigation. The site
MUST publish exactly the listed pages and no other page from `docs/`.

#### Scenario: A listed page is published under its section
- **WHEN** `docs/site.toml` lists `hpc.md` titled "HPC clusters" in the Reference section
- **THEN** the site's navigation shows "HPC clusters" under Reference, and the page is
  `docs/hpc.md` from the published release

#### Scenario: An excluded page is not published
- **WHEN** `docs/site.toml` excludes `releasing.md`
- **THEN** the site has no page for it, and a published page linking to it links to the
  file on GitHub at the published release

### Requirement: Every doc is listed or excluded
Every `docs/*.md` file MUST appear in `docs/site.toml` exactly once, either as a published
page or as excluded, and every page the manifest lists MUST exist. The `docs` lint MUST fail
otherwise, without network access or third-party packages, so it can run in PR CI and in the
pre-commit hook.

#### Scenario: A new doc that is neither listed nor excluded
- **WHEN** an MR adds `docs/new-guide.md` and leaves `docs/site.toml` unchanged
- **THEN** `docs:manifest` fails and names `new-guide.md`

#### Scenario: A published doc is renamed
- **WHEN** an MR renames `docs/hpc.md` to `docs/clusters.md` and leaves `docs/site.toml`
  unchanged
- **THEN** `docs:manifest` fails, naming `hpc.md` as listed but missing and `clusters.md` as
  neither listed nor excluded

#### Scenario: A doc is both listed and excluded
- **WHEN** `docs/site.toml` lists `hpc.md` and also excludes it
- **THEN** `docs:manifest` fails and names `hpc.md`

### Requirement: Published pages build cleanly before they merge
PR CI SHALL build the published pages the way the site renders them, in strict mode, and MUST
fail when a published page has a link or `#anchor` into another published page that does
not resolve. The build MUST use no credentials.

#### Scenario: A broken anchor into a published page
- **WHEN** a published page links to `skill-onboarding.md#a-heading-that-was-renamed`
- **THEN** `docs:site` fails and names the page and the anchor

#### Scenario: A link into the source tree
- **WHEN** a published page links to `../backend/src/vista_backend/agents/agents.py`
- **THEN** the published page links to that file on GitHub at the published release's
  commit, and `docs:links` still checks that the file exists

### Requirement: The site shows the latest release
The site SHALL publish the docs of VISTA's latest published release, never a pre-release and
never `main`, and SHALL pick up a newly published release within a day. A release MUST carry
the manifest and the publishing step that the site runs for it, so a change to either takes
effect on the site with the release that contains it, without a change to the site's repo.

#### Scenario: A docs change on main
- **WHEN** a doc changes on `main` and no release has been published since
- **THEN** the site still shows the page as it was in the latest release

#### Scenario: A new release
- **WHEN** a release is published
- **THEN** within a day the site shows its docs, laid out by its `docs/site.toml`

#### Scenario: A pre-release
- **WHEN** a pre-release is published after the latest release
- **THEN** the site keeps showing the latest release's docs

### Requirement: A published page says where to edit it
Each published page SHALL say that it comes from the VISTA repository, name its file under
`docs/` and the release it shows, and link to that file on GitHub.

#### Scenario: A reader looks for the source
- **WHEN** a reader opens a published page
- **THEN** a note below its title names the file (for example `docs/hpc.md`) and the
  release (for example `v0.2.0`), linking to the file on GitHub at that release
