# Releasing VISTA

Release packages are built by GitHub Actions on the public mirror,
`github.com/Genesis-VISTA/vista`, by [`.github/workflows/release.yml`](../.github/workflows/release.yml).
The canonical repository is still GitLab. The mirror copies every branch and tag to GitHub and
overwrites anything committed only there, so every change, including to the workflow, lands on
GitLab. The design is in the OpenSpec change `github-release-builds`.

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

3. If a platform fails, no draft is created. Fix the cause on GitLab, then delete the tag on
   both GitLab and GitHub and push it again, or push a new tag. A re-run for a tag whose draft
   already exists replaces its assets and its generated notes, and keeps what you wrote under
   "What's changed".

## Checking the draft

Drafts are visible only to people with write access to the repository.

```bash
gh release view v0.2.0 -R Genesis-VISTA/vista
```

Check that it has three archives, each with its `.sha256` file, and that the notes name the
right build-inputs commit.

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
retrieval reported as `ok`, not `skip`. Then tick the macOS line under "Verified on real
hardware" in the draft.

## Publishing

1. Write the "What's changed" section in the draft's notes.
2. Tick the real-hardware checklist.
3. Publish it, in the release page's editor or with:

   ```bash
   gh release edit v0.2.0 -R Genesis-VISTA/vista --draft=false
   ```

Redistribution of the AI-safety PDFs, the embedding weights and `amscrot-py` must be cleared
before the first release is published.

**Rollback.** Delete the release and its tag. The workflow can be disabled in the repository's
Actions settings without a commit.

## A manual build

From the Actions tab, or:

```bash
gh workflow run release.yml -R Genesis-VISTA/vista --ref main
```

It builds all three packages into artifacts kept for 7 days, and creates no release. A manual
build's version is the last release tag plus the commits since it, such as `0.2.0+3.g1a2b3c4`.

## The build inputs

The AI-safety corpus and its prebuilt Chroma store come from the private
`Genesis-VISTA/vista-build-inputs`, laid out as the build's flags expect:

```
vista-data/ai-safety/   # the PDFs, for --payload
rag_db/                 # the Chroma store with its citation metadata, for --vector-store
```

It has no releases and is never offered as a download of its own. Its contents ship only inside
the packages. Each package job checks it out at the commit pinned in
[`.github/build-inputs.env`](../.github/build-inputs.env), using the `BUILD_INPUTS_DEPLOY_KEY`
secret or, failing that, `BUILD_INPUTS_TOKEN` (below). Until that file holds a real commit, the
workflow stops with "no build inputs pinned".

### Updating them

Do this when the corpus changes. It needs ORNL access to the corpus (`VISTA_DATA_TOKEN`, or a
local copy for `--payload`) and an LLM key for citation extraction, both through the repo-root
`.env` as for any build.

1. Build a package that indexes the corpus itself. Leave out `--vector-store`:

   ```bash
   ./scripts/build_local_package.sh            # or: --payload <a vista-data tree>
   ```

2. Take the corpus and the new store out of the package's payload:

   ```bash
   out=$(mktemp -d)
   tar -xf dist/vista-<version>-<platform>.tar.gz -C "$out" --strip-components 2 \
     vista-<version>-<platform>/payload/payload.tar
   tar -xf "$out/payload.tar" -C "$out" vista-data/ai-safety knowledge-bases/ai-safety/rag_db
   ```

3. Replace both directories in a checkout of the inputs repo, commit, and push. **Never
   force-push or rewrite its history.** Every past release's pin must stay resolvable. Nothing
   enforces this: the organisation's free plan allows no branch protection on a private repo,
   so it rests on this rule alone.

   ```bash
   git clone git@github.com:Genesis-VISTA/vista-build-inputs.git && cd vista-build-inputs
   rm -rf vista-data/ai-safety rag_db
   mkdir -p vista-data && cp -R "$out/vista-data/ai-safety" vista-data/
   cp -R "$out/knowledge-bases/ai-safety/rag_db" rag_db
   git add -A && git commit -m "Update the AI-safety corpus and store" && git push
   git rev-parse HEAD
   ```

4. Set `BUILD_INPUTS_COMMIT` in `.github/build-inputs.env` to that full hash, and commit it on
   GitLab.

5. Check the pin before relying on it. A clone at that commit must pass the build's preflight:

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
| `AMSC_GIT_TOKEN` | secret | A gitlab.com token that can read the private `amscrot-py` repository (`amsc2/…/amsc-isro-toolkit`), scope `read_repository` only |
| `AMSC_GIT_USER` | variable, optional | The token's username. Leave it unset for a personal or project access token. A deploy token needs its own username here |
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

Set the token with `gh secret set AMSC_GIT_TOKEN -R Genesis-VISTA/vista`, which prompts for the
value rather than taking it on the command line.

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
   rehearsal repository. Push it again after a commit to test the draft update.
4. Delete the draft, the tag and the repository afterwards.

`RELEASE_PLATFORMS` leaves out macOS, because private repositories get no `ubuntu-24.04-arm`
runner for the arm64 image, and pay a 10× multiplier for macOS minutes. Linux minutes count 1×
and Windows 2× against your personal quota. So a rehearsal does not test the mac job, the arm64
image build, or Genesis-VISTA's organisation Actions policies. Those are first exercised by a
manual run on the public mirror. The release job refuses to create a release when
`RELEASE_PLATFORMS` is set on a public repository.

## The runner probe

[`sam-baumann/vista-runner-probe`](https://github.com/sam-baumann/vista-runner-probe) reports what
each hosted runner offers: the hypervisor, KVM, glibc, disk and network, plus a real microVM
boot. Its results are recorded in the design. Rerun it whenever a runner pinned in
`release.yml` changes, before relying on the new one:

```bash
gh workflow run runner-probe.yml -R sam-baumann/vista-runner-probe
```
