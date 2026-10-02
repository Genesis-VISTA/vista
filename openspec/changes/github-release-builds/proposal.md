## Why

VISTA packages are built by hand, one platform at a time, on whichever machines a maintainer
has to hand, and handed out directly. Public GitHub releases are the planned way to
distribute VISTA, and the GitLab project is already push-mirrored to
`github.com/Genesis-VISTA/vista`, which goes public in the week of 2026-10-05. A public repo
gets free GitHub-hosted runners for Linux, macOS and Windows, so a tag can produce every
platform's package without anyone building on three machines.

## What Changes

- A GitHub Actions workflow, committed under `.github/workflows/` in the GitLab repo and
  carried to GitHub by the existing mirror, builds the linux-x86_64, mac-arm64 and
  windows-x86_64 packages with `scripts/build_local_package.sh`.
- Pushing a `v*` tag to GitLab creates a **draft** GitHub release with the three archives and
  their `.sha256` files, and only if all three platforms built. A tag with a prerelease suffix
  (`v0.2.0-rc1`) makes a prerelease. A maintainer publishes the draft by hand. A manual
  `workflow_dispatch` run builds any branch into short-lived workflow artifacts and creates no
  release.
- A prep job builds the code-execution sandbox image natively for amd64 and arm64, and the
  platform jobs take it as `--sandbox-image`. macOS runners have no Docker, and Windows
  runners cannot build Linux images.
- The AI-safety corpus and its prebuilt Chroma store come from a small **private** repo,
  `Genesis-VISTA/vista-build-inputs`, that holds only the files the build needs. The workflow
  checks it out at a commit pinned in this repo. code.ornl.gov is unreachable from GitHub's
  runners, and building the store in CI would need an LLM credential there. Nothing is
  released from the inputs repo, and it is never offered as a download of its own. A
  maintainer updates it by hand when the corpus changes, following documented steps.
- The package version comes from the tag. `v0.2.0` builds `vista-0.2.0-<os>-<arch>`. Local
  builds take their version from `git describe --tags` instead of the hardcoded `0.1.0+sha`.
  The first release is `v0.2.0`.
- The embedding weights stay a build-time Hugging Face download, pinned to one revision so
  every release ships the weights the bundled store was embedded with.
- The build deletes its staging tree before the smoke test, which brings peak disk under what
  hosted runners have.
- **Verification on hosted runners.** Hosted macOS and Windows runners are VMs without nested
  virtualisation, so the code-execution sandbox cannot run there. A new, explicit, build-only
  option lets the smoke test run every check that does not need the sandbox, and report the
  ones that do as skipped. CI uses it only on those two platforms. Linux runners have KVM and
  run the full smoke test. Before the draft is published, a maintainer runs the full smoke
  test on a real Mac and a real Windows machine.
- **Verifying before building.** A manual, secret-free runner probe reports what each hosted
  runner actually offers: hypervisor, KVM, glibc, disk and network. It runs first, in a
  separate private repo (`sam-baumann/vista-runner-probe`) so VISTA's history stays clean, and
  the workflow is written against facts. Each package job's steps
  live in one script that runs the same on a maintainer's Mac, and `act` exercises the Linux
  jobs locally.
- The release notes are generated from a template: version, platforms, per-archive sha256,
  the build-inputs commit and install instructions. They include the macOS Gatekeeper
  workaround, because the Mac package stays ad-hoc signed with no Developer ID or
  notarization.

## Capabilities

### New Capabilities

- `release-builds`: how tagged releases of VISTA packages are built, verified, versioned and
  published on GitHub. This covers the triggers, the platform set, inputs from outside the
  repo, the draft-then-publish gate and the release notes.

### Modified Capabilities

- `laptop-distribution`: "Built and verified on its own platform" gains an explicit,
  build-only verification mode for hosts that cannot run the sandbox. Sandbox-dependent
  checks are reported as skipped rather than passed, and the launcher is allowed to start
  without a hypervisor only under that mode. A new "Build identifier" requirement states the
  version a build takes when none is given: the last release tag plus the commits since it.

## Impact

- **New files:** `.github/workflows/release.yml`, `.github/scripts/package.sh`, a release-notes template, `.actrc`, and a maintainer doc
  (`docs/releasing.md`) for updating the build inputs, running the workflow locally and the
  pre-publish check.
- **Changed scripts:**
  - `scripts/build_local_package.sh`: version fallback, staging removed before the smoke test,
    the Hugging Face revision pin, and the new verification option.
  - `scripts/smoke_test_package.sh`: sandbox-dependent checks skippable under that option.
  - `scripts/package_launcher.sh` and `.ps1`: the hypervisor refusal is bypassed under the
    build-only option, never otherwise.
- **Weights pin:** `vista_mcp_server/config.py`, `rag_mcp.py` and `build_rag.py` load the
  embedding model at the pinned revision. These are the same weights as today, made explicit.
- **Outside VISTA:** the runner probe lives in the private `sam-baumann/vista-runner-probe`.
- **External setup:**
  - A private `Genesis-VISTA/vista-build-inputs` repo.
  - Two GitHub secrets, both read-only and each scoped to one repo: a token for the private
    `amscrot-py` repo on gitlab.com, and a deploy key for `vista-build-inputs`.
  - Actions enabled on the mirror once it is public.
- **Unchanged:** GitLab CI, the runtime behaviour of an installed package, and the
  researcher-facing launcher behaviour.
- **Out of scope:**
  - The curl | bash and `irm | iex` install script (a later change).
  - Showing the version in the UI.
  - linux-arm64 and mac-x86_64 packages.
  - Developer ID signing.
  - Packages built with `--science-projects`.
- **Dependency outside this change:** publishing a release depends on the team clearing
  redistribution of the AI-safety PDFs, the `harrier-oss-v1-270m` weights and `amscrot-py`.
