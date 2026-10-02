## Context

The proposal's Why section gives the motivation. This section covers only the current state
that shapes the approach.

- **One script builds everything.** `scripts/build_local_package.sh` builds a package for the
  host it runs on and then smoke-tests it (`scripts/smoke_test_package.sh`). It already
  accepts the three inputs CI cannot produce for itself: `--sandbox-image TAR` (checked
  against the target architecture), `--payload DIR` and `--vector-store DIR`
  (`check_store_matches_corpus` refuses a store that cites papers the payload lacks).
- **Canonical repo and mirror.** The canonical repo is the private GitLab project. It
  push-mirrors every branch and tag to `github.com/Genesis-VISTA/vista` with "keep divergent
  refs" off, so the GitLab state always wins. GitHub releases are not git refs, and the
  mirror never touches them.
- **Linux glibc floor.** The bundled sandbox runtime (`msb`) needs glibc ≥ 2.39
  (`build_local_package.sh:1412`), so a Linux build host must be Ubuntu 24.04 or newer.
- **The sandbox is a microVM.**
  - The Linux launcher refuses to start without `/dev/kvm`, and the Windows launcher refuses
    when `msb doctor` is not ready. The macOS launcher has no check, because the Hypervisor
    framework is always present on bare metal.
  - Without a sandbox, the `dev_mcp_server` lifespan fails, so every agent tool call fails,
    retrieval included (`package_launcher.sh:101`).
  - GitHub's hosted Linux runners expose KVM. The hosted macOS arm64 runners are M1 VMs
    without nested virtualisation. The hosted Windows runners do not offer nested
    virtualisation either.
- **Peak disk.** A mac-arm64 package is 4.0 GB unpacked and 1.7 GB archived. The staging tree
  is removed only after the smoke test, so peak disk is about 13–14 GB against a documented
  14 GB on hosted runners.
- **What the build needs from outside.**
  - The AI-safety corpus, from code.ornl.gov, which the runners cannot reach.
  - An LLM key, to index citations.
  - Read access to the private `amscrot-py` repo on gitlab.com. `palisade` is public now.
  - The embedding weights, from Hugging Face.

## Goals / Non-Goals

**Goals:**

- One workflow file that builds, verifies and drafts a release from a tag, with every
  external input pinned.
- Change the build scripts only where hosted runners force it: version fallback, disk, the
  sandbox-less verification mode, and the weights pin. Each change also stands on its own
  for local builds.

**Non-Goals:**

- Replacing or touching GitLab CI. It stays the correctness gate. This workflow is
  packaging only.
- Running the workflow on pull requests. No PRs exist on the mirror, and keeping
  `pull_request` out means fork code never runs with the secret.
- Caching builds between runs. Release builds are rare, and reproducibility from pins
  matters more than speed.

## Decisions

### D1: The workflow lives in the GitLab repo

`.github/workflows/release.yml` is committed on GitLab and arrives on GitHub through the
mirror. GitLab CI ignores `.github/`. Editing it on GitHub would be overwritten at the next
sync, so it is never done.
*Alternative:* a separate GitHub-only repo holding the workflow and checking out VISTA. That
means two places to change, and the tag-to-workflow link becomes indirect.

### D2: Job graph

```
sandbox-image (matrix: amd64 on ubuntu-24.04, arm64 on ubuntu-24.04-arm)
    └─► package (matrix: linux-x86 ubuntu-24.04 · mac-arm64 macos-15 · win-x86 windows-2025)
            └─► release (tags only; needs every package job)
```

- **`sandbox-image`** runs `docker buildx build` on the
  `mcp_servers/dev_mcp_server/.../docker/Dockerfile`, then `docker save`, and uploads
  `sandbox-image-<arch>.tar` as a one-day artifact. Native runners are used because QEMU
  emulation of arm64 is 5–10× slower.
- **`package`**:
  1. Downloads the image for its target (amd64 for Linux and Windows, arm64 for macOS).
  2. Downloads and verifies the corpus bundle (D5).
  3. Configures git access to `amscrot-py` from the secret.
  4. Runs `build_local_package.sh --payload … --vector-store … --sandbox-image …`, plus
     `--verify-without-sandbox` on macOS and Windows (D4).
  5. Uploads the archive and its `.sha256` file. On a manual run these are workflow
     artifacts with 7-day retention.
- **`release`** runs only for `refs/tags/v*`. It creates or updates a draft with
  `gh release`, using the `--prerelease` flag when the tag carries a suffix, and attaches the
  archives with `--clobber`, so a re-run of the same tag replaces assets rather than failing.
  Before uploading, it fails if any asset exceeds GitHub's 2 GiB per-file limit.

Workflow permissions default to `contents: read`. Only `release` gets `contents: write`. A
concurrency group per ref cancels a superseded manual run but never a tag run.

The three platforms use `fail-fast: false` so one failure still reports the others, and
`release` needs all three, which is what makes "no partial releases" hold.

### D3: Pinned runners

`ubuntu-24.04`, `ubuntu-24.04-arm`, `macos-15` and `windows-2025`. Never `-latest`.

- **Linux:** 24.04 is forced by the glibc floor, which overrides the 22.04 preference from
  planning.
- **macOS and Windows:** the build records the host's version as the floor
  (`target_floor`), so pinning is what keeps the floor stable.
- **Windows shell:** the job uses Git Bash, which the hosted image includes, as the script
  already requires.
- **Linux KVM access:** the Linux job adds the usual udev rule so the runner user can open
  `/dev/kvm`.

### D4: Verifying without the sandbox

There is a new build option, `--verify-without-sandbox`. Under it the build exports
`VISTA_VERIFY_WITHOUT_SANDBOX=1` to the smoke test only, following the existing
`VISTA_NO_WINDOW=1` pattern, which is likewise meant for the smoke test alone.

- **Launchers** (`package_launcher.sh` and `.ps1`): when the variable is set, the hypervisor
  refusal prints a one-line notice instead of exiting. Nothing a researcher runs sets it,
  and the notice keeps an accidental use visible.
- **Smoke test:**
  - Every check that needs the sandbox goes through the existing `skip` helper, which
    reports "skipped" with a reason and never "ok". Retrieval is one of them, because the
    sandbox server is part of every agent toolset.
  - Everything else still runs: service health, openapi, the UI reaching the backend, the
    science-data absence check, the version check and the window check.
  - The final line says the package was verified without the sandbox.
- **Build summary:** the build echoes the same statement, and the `release` job reads it to
  add the "verify on real hardware" line to the notes.

*Alternatives considered:*

- Self-hosted Mac and Windows runners: always-on hardware, and hardening a public repo's
  self-hosted runners.
- Paid larger runners: nested virtualisation is not guaranteed on macOS.
- Shipping Linux only.

All three were rejected in favour of the draft gate, where a maintainer runs the full smoke
test against the downloaded draft asset on a real machine.

### D5: The corpus bundle

The bundle is a `vista-build-inputs-<version>.tar.gz` asset on a release of the public
`Genesis-VISTA/vista-build-inputs` repo. It contains:

- `vista-data/ai-safety/`, the PDFs, laid out as `--payload` expects;
- `rag_db/`, the Chroma store with its citation metadata, which `--vector-store` expects.

The pin is a small checked-in file, `.github/build-inputs.env`, holding
`BUILD_INPUTS_VERSION` and `BUILD_INPUTS_SHA256`, which the workflow reads. A mismatch fails
the job before any build step.

To refresh the bundle, a maintainer runs the existing indexing locally with ORNL access and
an LLM key, packs the two directories, publishes a new release on the inputs repo and
updates the pin. The steps are documented in `docs/releasing.md`, not scripted (planning
decision Q20).

The bundle repo is separate because the GitLab mirror could prune tags that exist only on
the main mirror, and a release whose tag is pruned becomes orphaned.
*Alternatives:* GitLab's package registry, which needs a second secret in GitHub, or a
self-hosted step inside ORNL.

### D6: Version identifier

- **Tagged runs:** the workflow sets `VISTA_VERSION="${GITHUB_REF_NAME#v}"`.
- **Local builds:** without `VISTA_VERSION`, the build uses
  `git describe --tags --match 'v[0-9]*' --long`, reformatted into semver build metadata:
  `0.2.0+3.gabc1234`, plus `-dirty` as today.
- **No reachable tag:** the identifier is `0.0.0+g<sha>`.

Build metadata goes after `+` because the archive name already strips everything from the
`+` (`${VERSION%%+*}`). The archive name stays `vista-0.2.0-<os>-<arch>`, while the manifest
and the running app carry the full identifier. `VISTA_COMMIT` continues to work for tree
exports with no `.git`.

The five manifests' `0.1.0` stay placeholders, because nothing reads them (Q12). Hosted
checkouts are shallow, but tagged runs set the version explicitly, so `git describe` is
never needed there.

### D7: Pinning the embedding weights

Add `rag_model_revision` (a Hugging Face commit hash) next to `rag_model` in
`vista_mcp_server/config.py`, and pass it wherever the model is loaded. There are two such
places:

- `rag_mcp.py:130`, at query time.
- `build_rag.TextRAG` (`build_rag.py:782`), at index time. Its own default model name
  becomes a matching default revision, so the two cannot drift. The build reads it
alongside `rag_model` and calls `snapshot_download(model, revision=…)`. When it reuses the
host cache, it checks that the pinned snapshot is present and fails if it is not.

This pins the weights in one place, so dev indexing (the corpus bundle refresh), CI packages
and the running app all use the same weights. Pinning only in the build script would leave
local indexing on whatever `main` is today, and the store and weights could silently
diverge.
*Alternative:* write `refs/main` in the staged cache to point at the pinned snapshot, which
avoids any runtime change. It works offline, but hides the pin from dev.

### D8: Disk

`create_archive` is followed by removing the staging tree, unless `--keep-staging` is set,
before `run_smoke_test`. Nothing reads staging after the archive is written. That takes the
peak to about 10 GB. Each package job prints `df -h` at the start and after the build, so the
first runs show the real margin.

### D9: Release notes

`.github/release-notes.md` is a template with placeholders for the version, a table of
archives with their sha256 values, the bundle version, per-platform download/verify/run
steps, and the macOS quarantine workaround (`xattr -dr com.apple.quarantine`, or approve in
System Settings → Privacy & Security). The workaround is needed because the app is ad-hoc
signed (Q25). The template also has a "Verified on real hardware" checklist for macOS and
Windows, and a `## What's changed` placeholder. `release` fills it in with `envsubst` and a
few lines of shell.

## Risks / Trade-offs

- **[The macOS launcher's first run may need the hypervisor beyond the agent toolset.]**
  For example, the sandbox image import. If so, the smoke test fails even under
  `--verify-without-sandbox`.
  → Find out on the first manual run. If it happens, the import is gated by the same variable
  and reported as skipped.
- **[A hosted Windows runner might pass `msb doctor`.]** In that case skipping the sandbox
  checks there is needlessly weak.
  → The first manual run logs `msb doctor`. If the runner is ready, drop the flag for
  Windows. That's a one-line matrix change.
- **[The bypass variable leaks into a real run.]**
  → It is set only inside the smoke test's environment, the launcher prints a notice
  whenever it is honoured, and the spec's researcher scenario pins the refusal.
- **[The real-hardware check before publishing is a manual step that can be forgotten.]**
  → The draft's notes carry an unticked checklist for macOS and Windows, and
  `docs/releasing.md` makes ticking it the publishing step.
- **[An archive grows past 2 GiB.]** Today's is 1.7 GB.
  → The `release` job checks sizes and fails with the size named. Science packages are out
  of scope for exactly this reason.
- **[The disk margin is wrong on some runner.]**
  → The `df` logging (D8) shows it. On Linux there's a fallback that deletes the preinstalled
  toolchains.
- **[The inputs repo or its asset is deleted or replaced.]**
  → The sha256 pin fails the build rather than shipping a different corpus.
- **[Mac users who download in a browser hit Gatekeeper.]**
  → This is accepted (Q25). The notes carry the workaround, and the later curl installer
  avoids the quarantine flag altogether.

## Migration Plan

There is no data or runtime migration. The rollout order:

1. Land the script changes, the workflow, the template and `docs/releasing.md` on GitLab.
   The mirror carries them over. The workflow stays dormant until Actions is enabled.
2. Create `Genesis-VISTA/vista-build-inputs`, publish the first bundle from a maintainer's
   Mac, and commit the pin.
3. When the mirror goes public: enable Actions, and add the `AMSC_GIT_TOKEN` secret (a
   read-only deploy token).
4. Do a manual run on `main` and fix whatever the runners turn up: disk, the macOS first-run
   behaviour, `msb doctor` on Windows.
5. Push `v0.2.0-rc1`. Check that the draft prerelease and its notes are right, run the
   real-hardware smoke test, then delete the rc.
6. Push `v0.2.0` and publish once redistribution is cleared.

**Rollback:** delete the draft or release and the tag. The workflow can be disabled in
GitHub's settings without a commit.
