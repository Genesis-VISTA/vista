# Releasing VISTA

Release packages are built by GitHub Actions on the public mirror,
`github.com/Genesis-VISTA/vista`, by [`.github/workflows/release.yml`](../.github/workflows/release.yml).
The canonical repository is still GitLab. The mirror copies every branch and tag to GitHub and
overwrites anything committed only there, so every change, including to the workflow, lands on
GitLab. The design is in the OpenSpec change `github-release-builds`. Building a package on
your own machine, and the build's inputs and flags, are in
[building-packages.md](building-packages.md).

A `v*` tag builds three packages:

| platform | runner | verified |
|---|---|---|
| `linux-x86` | `ubuntu-24.04` | full smoke test, sandbox included |
| `mac-arm64` | `macos-15` | without the sandbox, because hosted macOS can't run it. A maintainer runs the full test before publishing |
| `win-x86` | `windows-2025` | full smoke test, sandbox included |

If all three build, the run creates a **draft** release. Nothing is published until a
maintainer does it by hand.

## Cutting a release

1. Tag the commit on GitLab and push the tag. A suffix such as `-rc1` makes a prerelease.

   ```bash
   git tag -a v0.2.0 -m "VISTA 0.2.0"
   git push origin v0.2.0
   ```

2. The mirror carries the tag to GitHub within a few minutes, and the workflow starts. Watch it:

   ```bash
   gh run list -R Genesis-VISTA/vista -w release
   gh run watch -R Genesis-VISTA/vista <run-id>
   ```

   The version comes from the tag, without the `v`. No file needs editing.

3. If a platform or the `data-compat` job fails, no draft is created. Fix the cause on GitLab, then delete the tag on
   both GitLab and GitHub and push it again, or push a new tag. A re-run for a tag whose draft
   already exists replaces its assets and its generated notes, and keeps what you wrote under
   "What's changed".

## Checking the draft

Drafts are visible only to people with write access to the repository.

```bash
gh release view v0.2.0 -R Genesis-VISTA/vista
```

Check that it has three archives, each with its `.sha256` file, plus `install.sh`,
`install.ps1`, `update.json` and `vista-schema.sqlite`, and that the notes name the right
build-inputs commit under "Build and verification".

`update.json` is what VISTA's update check reads to warn that an update resets local data:
its `data_compatible_from` is the oldest earlier release whose database this one opens, and
an install older than that is told its data will be reset. The workflow's `data-compat` job
writes it with [`.github/scripts/data-compat.sh`](../.github/scripts/data-compat.sh), which
runs this release's startup check against each earlier release's empty database, newest
first, and stops at the first refusal. Its log names the tag and the columns. If it
refuses the newest earlier release (`data_compatible_from` is this version), this release
resets every researcher's data: a `db/schemas.py` change since then was not additive. Make
sure that is intended before publishing.
`vista-schema.sqlite` is this release's own empty database, for the next release's check.

To run the check yourself before tagging (it builds each earlier release from its tag, so
it needs the tags fetched):

```bash
VERSION=0.2.5 REPO=Genesis-VISTA/vista .github/scripts/data-compat.sh /tmp/data-compat
VERSION=0.2.5 RELEASE_TAGS="v0.2.4 v0.2.3" .github/scripts/data-compat.sh /tmp/data-compat  # without GitHub
```

While the repository is private, its release assets need a signed-in download: plain `curl`
gets a 404, so the release notes' own commands work only once it is public. Use
`gh release download`, as below.

## The real-hardware smoke test (macOS)

CI verifies the macOS package without the sandbox, so retrieval goes unchecked there. Before
publishing, run the full smoke test on a real Mac, using the draft's own asset:

```bash
gh release download v0.2.0 -R Genesis-VISTA/vista -p 'vista-*-mac-arm64.tar.gz*' -D /tmp/rel
cd /tmp/rel && shasum -a 256 -c vista-0.2.0-mac-arm64.tar.gz.sha256
mkdir -p /tmp/rel/unpacked && tar -xf vista-0.2.0-mac-arm64.tar.gz -C /tmp/rel/unpacked
# from a VISTA checkout:
./scripts/smoke_test_package.sh /tmp/rel/unpacked/vista-0.2.0-mac-arm64 "$(mktemp -d /tmp/vista-smoke.XXXXXX)"
```

Pass a short state directory under `/tmp`, as above. The sandbox derives a Unix socket path from
it, and macOS caps those at 104 bytes. The run must end with `all checks passed`, with
retrieval reported as `ok`, not `skip`. Then tick the macOS line under "Build and
verification" in the draft. That section is collapsed, so open it to see the box.

## Publishing

1. Write the "What's changed" section in the draft's notes.
2. Tick the real-hardware checklist, in the collapsed "Build and verification" section.
3. Publish it, in the release page's editor or with:

   ```bash
   gh release edit v0.2.0 -R Genesis-VISTA/vista --draft=false
   ```

Redistribution of the OLCF documentation, the embedding weights and `amscrot-py` must be cleared
before the first release is published.

**Rollback.** Delete the release and its tag. The workflow can be disabled in the repository's
Actions settings without a commit.

## The one-line installers

Each release carries `install.sh` (macOS and Linux) and `install.ps1` (Windows), with that
release's version written in by `.github/scripts/render-installers.sh`. The notes lead with
them, pinned to the release's own tag. The README and [installing.md](installing.md) use `releases/latest/download/`, which
GitHub points at the newest published release, skipping drafts and prereleases, so it works
from the first published non-prerelease on.

```bash
curl -fsSL https://github.com/Genesis-VISTA/vista/releases/latest/download/install.sh | bash
```
```powershell
powershell -ExecutionPolicy Bypass -c "irm https://github.com/Genesis-VISTA/vista/releases/latest/download/install.ps1 | iex"
```

They do what the notes' manual steps do: download the platform's archive, check it against
its `.sha256`, unpack it beside the install and rename it into place, then open VISTA. Each
platform installs into one fixed folder, so an upgrade replaces it: macOS into
`/Applications/VISTA` (`~/Applications/VISTA` where that cannot be written), Linux into
`~/.local/share/vista/app` with an app-menu entry, and Windows into `%LOCALAPPDATA%\VISTA\app`
with a Start menu entry; on macOS and Linux `~/.local/bin/vista` links to the terminal
launcher. They refuse while VISTA is running from
a folder they would replace. What a researcher sees is in [installing.md](installing.md). A
re-run of the installed version
downloads nothing. An upgrade removes the old package only once the new one is in place, and
state in `VISTA_HOME` is never touched.

They are tested three ways: `scripts/test_install.sh` and `scripts/test_install.ps1` against
fake packages, in GitLab CI (`install:test`) and with `./scripts/ci-local.sh install`; and each
package job installs its own archive through its platform's installer after the smoke test.

## A manual build

From the Actions tab, or:

```bash
gh workflow run release.yml -R Genesis-VISTA/vista --ref main
```

It builds all three packages into artifacts kept for 7 days, and creates no release. A manual
build's version is the last release tag plus the commits since it, such as `0.2.0+3.g1a2b3c4`.

## The build inputs

The OLCF docs corpus and its prebuilt Chroma store come from the private
`Genesis-VISTA/vista-build-inputs`, laid out as the build's flags expect:

```
vista-data/olcf-docs/   # the Markdown sections and SOURCE, for --payload (no store)
rag_db/                 # the Chroma store with its descriptions, for --vector-store
```

It has no releases and is never offered as a download of its own. Its contents ship only inside
the packages. Each package job checks it out at the commit pinned in
[`.github/build-inputs.env`](../.github/build-inputs.env), using the `BUILD_INPUTS_DEPLOY_KEY`
secret or, failing that, `BUILD_INPUTS_TOKEN` (below). Until that file holds a real commit, the
workflow stops with "no build inputs pinned".

### Updating them

Do this when the OLCF documentation changes. The corpus is `olcf/olcf-user-docs` converted to
Markdown, one file per section, and `SOURCE` records the docs commit it came from. It needs an LLM
key for the descriptions (one model call per file, through the repo-root `.env` as for any build)
and, to push, access to the private repositories. The first refresh, of 144 pages, made 790 files,
1,152 chunks and a 33 MB store, and indexing took about 17 minutes with OLCF Inference's
`gpt-oss-120b`.

1. Clone the docs at the commit you chose, and convert them. The converter builds the checkout
   with Sphinx, using the pins from its `contributing/index.rst` in a throwaway `uv` environment,
   and needs the network:

   ```bash
   git clone https://github.com/olcf/olcf-user-docs && git -C olcf-user-docs checkout <commit>
   uv run scripts/olcf_docs/convert.py olcf-user-docs olcf-docs
   ```

   `olcf-docs/` now holds the section files and `SOURCE`. Sphinx warnings are reported, not
   fatal; read them.

2. Index it. Build a package from the converted folder, leaving out `--vector-store`, so the
   build's own indexer writes a description for each file:

   ```bash
   mkdir -p /tmp/vista-data && rm -rf /tmp/vista-data/olcf-docs && cp -R olcf-docs /tmp/vista-data/
   ./scripts/build_local_package.sh --payload /tmp/vista-data
   out=$(mktemp -d)
   tar -xf dist/vista-<version>-<platform>.tar.gz -C "$out" --strip-components 2 \
     vista-<version>-<platform>/payload/payload.tar
   tar -xf "$out/payload.tar" -C "$out" vista-data/olcf-docs knowledge-bases/olcf-docs/rag_db
   ```

   The store is in the payload at `knowledge-bases/olcf-docs/rag_db`. (Calling
   `vista_backend.utils.indexer.index_publications` directly gives the same store.)

3. Run the retrieval gate on that store. It asks the questions in
   `scripts/olcf_docs/questions.toml` the way an agent does and checks that the expected file is
   among the top 5 hits. It exits 0 when all pass, 1 when any fails, 2 on bad input. **It must
   pass before anything is committed.** Add a question when the refresh brings a topic worth
   guarding.

   ```bash
   uv run --project mcp_servers/vista_mcp_server scripts/olcf_docs/check_retrieval.py \
     "$out/knowledge-bases/olcf-docs/rag_db"
   ```

4. Commit the corpus to the two private repositories. **Never force-push or rewrite either's
   history.** Every past release's pin must stay resolvable. Nothing enforces this: the
   organisation's free plan allows no branch protection on a private repo, so it rests on this
   rule alone.

   - `v28/vista-data` (code.ornl.gov), for dev seeding: replace `olcf-docs/` with the Markdown,
     `SOURCE` and the new `rag_db/`.
   - `Genesis-VISTA/vista-build-inputs`: replace `vista-data/olcf-docs/` (Markdown and `SOURCE`,
     no store) and the top-level `rag_db/`.

   ```bash
   git clone git@github.com:Genesis-VISTA/vista-build-inputs.git && cd vista-build-inputs
   rm -rf vista-data/olcf-docs rag_db
   mkdir -p vista-data && cp -R "$out/vista-data/olcf-docs" vista-data/
   cp -R "$out/knowledge-bases/olcf-docs/rag_db" rag_db
   git add -A && git commit -m "Update the OLCF docs corpus and store" && git push
   git rev-parse HEAD
   ```

5. Set `BUILD_INPUTS_COMMIT` in `.github/build-inputs.env` to that full hash, and commit it on
   GitLab.

6. Check the pin before relying on it. A clone at that commit must pass the build's preflight:

   ```bash
   ./scripts/build_local_package.sh --check \
     --payload <inputs checkout>/vista-data --vector-store <inputs checkout>/rag_db
   ```

The store is tens of MB, so each update grows the repo's history by about that much. At an
update every few months, plain git is fine.

## Secrets and variables

Set on `Genesis-VISTA/vista` (and on a rehearsal repo, below), under Settings → Secrets and
variables → Actions. Each repository that builds gets a deploy key of its own, so one can be
revoked without touching the others.

| name | kind | what it is |
|---|---|---|
| `BUILD_INPUTS_DEPLOY_KEY` | secret | The private half of a read-only deploy key on `vista-build-inputs`. Preferred, but needs deploy keys enabled for the organisation |
| `BUILD_INPUTS_TOKEN` | secret | Used only when there is no deploy key: a fine-grained token that can read `vista-build-inputs`' contents and nothing else |
| `AMSC_GIT_TOKEN` | secret | A maintainer's gitlab.com fine-grained token that can only download the private `amscrot-py` repository's code (below) |
| `RELEASE_PLATFORMS` | variable | **Leave unset on the public repository.** Only a private rehearsal sets it (below) |

One of the two inputs credentials must be set, and the package jobs stop early, naming both,
when neither is.

**Deploy keys are disabled for the Genesis-VISTA organisation**, as of 2026-10-02.
`gh repo deploy-key add` then fails with "Deploy keys are disabled for this repository", and
only an organisation owner can allow them (Organisation settings → Member privileges). Until
one does, use the token.

Creating the token: at github.com → Settings → Developer settings → Fine-grained tokens, set
the resource owner to Genesis-VISTA, give it access to `vista-build-inputs` only, with
**Contents: read-only** and no other permission, and pick an expiry. The organisation may have
to approve it. Then store it with `gh secret set BUILD_INPUTS_TOKEN -R <repo>`, which prompts
for the value. It belongs to the person who made it and stops working when it expires or
they leave the organisation, which is why the deploy key is preferred.

Creating the deploy key, once they are allowed:

```bash
repo=Genesis-VISTA/vista        # the repository whose workflow reads the inputs
ssh-keygen -t ed25519 -N '' -C "release builds for $repo" -f /tmp/vista-build-inputs-key
gh repo deploy-key add /tmp/vista-build-inputs-key.pub -R Genesis-VISTA/vista-build-inputs \
  --title "release builds: $repo (read-only)"
gh secret set BUILD_INPUTS_DEPLOY_KEY -R "$repo" < /tmp/vista-build-inputs-key
rm /tmp/vista-build-inputs-key /tmp/vista-build-inputs-key.pub
```

`gh repo deploy-key add` makes a read-only key unless it is given `--allow-write`. If the key
leaks, it opens only that one repository, whose contents ship inside every public package anyway.
Rotate it by repeating the steps and deleting the old key, which
`gh repo deploy-key list -R Genesis-VISTA/vista-build-inputs` names by its title.

**Creating the GitLab token.** It is a fine-grained personal access token belonging to a
maintainer with at least Reporter access to `amsc2/…/amsc-isro-toolkit`, the repository that
provides `amscrot-py`. A project deploy token would not depend on a person, but needs a
Maintainer of that project to create it, and the fine-grained token is already confined to one
project's code. At gitlab.com → Edit profile → Access → Personal access tokens → Generate token →
**Fine-grained token**:

- **Group and project access:** only the selected project
  `amsc2/infrastructure-and-services/infrastructure-services/resource-orchestration/amsc-isro-toolkit`;
- **Resource permissions:** on the Group and project tab, **Code → Download**, and nothing else;
- **Expiration date:** GitLab allows at most 365 days.

Check it in a terminal before storing it. `read -s` keeps it off the screen:

```bash
read -rs T && git ls-remote "https://oauth2:$T@gitlab.com/amsc2/infrastructure-and-services/infrastructure-services/resource-orchestration/amsc-isro-toolkit.git" HEAD && echo TOKEN-OK; unset T
```

Then store it with `gh secret set AMSC_GIT_TOKEN -R Genesis-VISTA/vista`, which prompts for the
value rather than taking it on the command line. Run both in an interactive terminal: without one,
`gh secret set` reads standard input and can store an empty secret.

**Before the token expires**, or if its owner leaves the project, create a new one and set the
secret again. Otherwise every package job fails its preflight with "no access to the amsc2
repository that provides amscrot-py".

## Running a package job's steps locally

Each package job does its checkouts and downloads, then runs
[`.github/scripts/package.sh`](../.github/scripts/package.sh). Run that script yourself to
reproduce a job exactly. It needs an inputs checkout and a sandbox image for this machine's
architecture, built the way the workflow's `sandbox-image` job builds it:

```bash
ctx=mcp_servers/dev_mcp_server/src/dev_mcp_server/docker
docker buildx build --load -t vista-sandbox:latest -f "$ctx/Dockerfile" "$ctx"
docker save -o /tmp/sandbox-image.tar vista-sandbox:latest

BUILD_INPUTS_DIR=<inputs checkout> SANDBOX_IMAGE=/tmp/sandbox-image.tar \
  .github/scripts/package.sh
```

Add `VERIFY_WITHOUT_SANDBOX=1` to run it the way the macOS job does. Without `AMSC_GIT_TOKEN`,
it uses your own git credentials for `amscrot-py`.

## Rehearsing a workflow change

A change to the workflow can be rehearsed before it lands on GitLab, in a private repository on
your own account (design D12):

1. Create a private repository, such as `<you>/vista-release-rehearsal`, and push the branch to
   it as `main`, so `workflow_dispatch` sees the workflow.
2. Add the `AMSC_GIT_TOKEN` secret and one inputs credential (`BUILD_INPUTS_DEPLOY_KEY` or
   `BUILD_INPUTS_TOKEN`), and set the variable `RELEASE_PLATFORMS` to
   `["linux-x86","win-x86"]`.
3. Run the workflow by hand. To test the release job, push a tag such as `v0.2.0-rc0` to the
   rehearsal repository. It needs only one platform, so narrow `RELEASE_PLATFORMS` to
   `["linux-x86"]` first. To test the draft update, add a line under "What's changed" in the
   draft, then rerun only the release job, which reuses the run's packages:
   `gh run rerun --job <release job id> -R <repo>`. The line should survive and the assets be
   replaced.
4. Delete the draft, the tag and the repository afterwards.

`RELEASE_PLATFORMS` leaves out macOS, because private repositories pay a 10× multiplier for
macOS minutes. Linux minutes count 1× and Windows 2× against your personal quota. So a
rehearsal does not test the mac job or Genesis-VISTA's organisation Actions policies. (The
design assumed private repositories get no `ubuntu-24.04-arm` runner either, but the mirror's
first run, still private on 2026-10-05, got one, and built the arm64 image on it.) The release
job refuses to create a release when `RELEASE_PLATFORMS` is set on a public repository.

**The 2026-10-05 rehearsal** (in a personal repo, since deleted) took six runs and about
190 billed minutes: 77 Linux job-minutes and 55 Windows ones at 2×. A green run's jobs took:

| job | minutes |
|---|---|
| plan | under 1 |
| sandbox-image (amd64) | 2 |
| package (linux-x86) | 10, including the disk clean-up |
| package (win-x86) | 21 |
| release (one Linux archive) | 2 to 3, mostly the upload |

So a manual run takes about 25 minutes, all set by Windows. Its fixes, all on this branch:

- `astral-sh/setup-uv` publishes no floating major tags after v7, so it is pinned to a full
  version (`v10.2.0`).
- The MCP app build ran `npx` through `execFileSync`, which cannot find `npx.cmd` on Windows.
  It now runs vite's own script under the same Node.
- Git Bash has `sha256sum` and no `shasum`. The checksum step uses whichever is present.
- The Linux runner starts with about 14 GB free, and the smoke test's sandbox image import ran
  out of space. The package job first deletes preinstalled toolchains it never uses, which
  leaves about 41 GB, and the build ends with 34 GB still free. Windows' `D:` has over 100 GB.
- The smoke test now prints the end of `setup.log` as well as the launcher's output when the
  launcher fails, since a hosted runner's temporary directory is gone with the job.

## Changing a runner

The runners pinned in `release.yml` were chosen from a one-off probe of what each hosted
runner offers (the hypervisor, KVM, glibc, disk and network), recorded in the OpenSpec change's
design. Before relying on a new runner, make a manual run on it. Each package job prints
`df -h` before and after the build, and on Windows `msb doctor`, and the smoke test says
whether the sandbox booted.
