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
  - The runner probe (D10) found that GitHub's hosted Linux runners expose KVM and boot the
    sandbox. The hosted Windows runners have the hypervisor platform enabled, pass `msb
    doctor` and boot it too. The hosted macOS arm64 runners are VMs without nested
    virtualisation, and the sandbox cannot start there.
- **Peak disk.** A mac-arm64 package is 4.0 GB unpacked and 1.7 GB archived. The staging tree
  is removed only after the smoke test, so peak disk is about 13–14 GB against a documented
  14 GB on hosted runners.
- **What the build needs from outside.**
  - The AI-safety corpus, from code.ornl.gov. The runners can reach the host (D10), but
    fetching from it would need an ORNL credential among a public repo's secrets.
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
  2. Checks out the private build-inputs repo at the pinned commit (D5).
  3. Configures git access to `amscrot-py` from the secret.
  4. Runs `build_local_package.sh --payload … --vector-store … --sandbox-image …`, plus
     `--verify-without-sandbox` on macOS only (D4).
  5. Uploads the archive and its `.sha256` file. On a manual run these are workflow
     artifacts with 7-day retention.
- **`release`** runs only for `refs/tags/v*`. It creates or updates a draft with
  `gh release`, using the `--prerelease` flag when the tag carries a suffix, and attaches the
  archives with `--clobber`, so a re-run of the same tag replaces assets rather than failing.
  Before uploading, it fails if any asset exceeds GitHub's 2 GiB per-file limit.

Workflow permissions default to `contents: read`. Only `release` gets `contents: write`. A
concurrency group per ref cancels a superseded manual run but never a tag run.

The three platforms use `fail-fast: false` so one failure still reports the others, and
`release` needs all three, which is what makes "no partial releases" hold. The one exception
is the private rehearsal (D12), which narrows the platform set through a repository variable
that a public repository refuses to release with.

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

- **Where it applies.** CI uses it only on macOS, the one hosted runner that cannot boot the
  sandbox (D10). Linux and Windows run the full smoke test.
- **Launcher** (`package_launcher.sh`): when the variable is set, the `/dev/kvm` refusal
  prints a one-line notice instead of exiting. Nothing a researcher runs sets the variable,
  and the notice keeps an accidental use visible. The bypass is what lets a Linux build verify
  without the sandbox locally, for example under `act` (7.2). The macOS launcher has no
  hypervisor check to bypass. `package_launcher.ps1` is left unchanged, because no Windows
  build uses the option.
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

- Self-hosted Mac runners: always-on hardware, and hardening a public repo's
  self-hosted runners.
- Paid larger runners: nested virtualisation is not guaranteed on macOS.
- Shipping Linux only.

All three were rejected in favour of the draft gate, where a maintainer runs the full smoke
test against the downloaded draft asset on a real machine.

### D5: The build inputs

The build inputs live in a private git repo, `Genesis-VISTA/vista-build-inputs`, that holds
only what the build needs, laid out as the flags expect:

- `vista-data/ai-safety/`: the PDFs, for `--payload`;
- `rag_db/`: the Chroma store with its citation metadata, for `--vector-store`.

It is a plain repo, with no releases and no tags, so nothing in it is published.

- **Pin.** A small checked-in file, `.github/build-inputs.env`, holds `BUILD_INPUTS_COMMIT`.
  Each package job checks the inputs repo out at that commit with `actions/checkout`, using
  `ssh-key: ${{ secrets.BUILD_INPUTS_DEPLOY_KEY }}`. A read-only deploy key belongs to the
  repo, not to a person, and opens nothing else.
- **Integrity.** A git commit hash already covers every file's contents, so a separate
  sha256 is not needed. A missing commit, or refused access, fails the job before any build
  step.
- **Updating the inputs.** A maintainer runs the existing indexing locally with ORNL access
  and an LLM key, replaces the two directories in a checkout of the inputs repo, commits,
  pushes, and updates the pin. The steps are documented in `docs/releasing.md`, not scripted.
  The maintainer decided against a script because updates are rare.
- **Size.** The store is tens of MB of binary files, so each update grows the repo's history
  by about that much. At an update every few months, plain git is fine, and Git LFS is not
  worth its setup.

*Alternatives:*

- Release assets on a public repo: that publishes the corpus on its own, which is not
  wanted.
- GitLab's package registry: it needs a gitlab.com token with wider scope.
- A self-hosted step inside ORNL.
- Fetching the corpus from code.ornl.gov in CI. The probe showed the host is reachable, but
  that puts an ORNL credential in a public repo's secrets, and the store would still need an
  LLM key to build. The maintainer chose the private inputs repo regardless.

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

The five manifests' `0.1.0` stay placeholders, because nothing reads them. Requiring them to
match the tag would make every release need a version-bump commit, and was rejected for that
reason. Hosted
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

This pins the weights in one place, so dev indexing (updating the build inputs), CI packages
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
archives with their sha256 values, the build-inputs commit, per-platform download/verify/run
steps, and the macOS quarantine workaround (`xattr -dr com.apple.quarantine`, or approve in
System Settings → Privacy & Security). The workaround is needed because the app is ad-hoc
signed: `codesign --sign -`, with no Developer ID and no notarization. The maintainer decided
to keep it that way, with no Developer ID work planned. The template also has a "Verified on real hardware" checklist for macOS, the one platform
CI verifies without the sandbox, and a `## What's changed` placeholder. `release` fills it in with `envsubst` and a
few lines of shell.

### D10: Probe the runners before building on them

The plan's riskiest assumptions are facts about GitHub's runners that no local setup can
reproduce:

- that macOS can't run the sandbox;
- whether Windows' `msb doctor` is ready;
- that Linux exposes KVM;
- the glibc version;
- the free disk;
- which hosts the runners can reach.

`runner-probe.yml` is a manual, secret-free workflow that only reports these. It runs `msb
doctor` from the same microsandbox version VISTA locks, so its answer is the one the launcher
would get.

**Where it lives.** It lives in its own private repo, `sam-baumann/vista-runner-probe`, not in
VISTA. It needs nothing from VISTA (no checkout, no secrets, no inputs), so VISTA's history
gets no probe commits. Hosted runner images are the same for personal and organisation
repos, so the results carry over. Organisation-level Actions policies on Genesis-VISTA are not
tested this way, and they surface at 10.1.

**Runners.** It has one job per hosted runner the release builds on: `ubuntu-24.04`,
`macos-15` and `windows-2025`. Standard `ubuntu-24.04-arm` runners are available only to
public repos, so the private probe leaves that runner out. The arm64 sandbox-image build is
exercised natively by `act` on Apple Silicon instead (7.1), and the first manual run on the
public mirror confirms it.

**Cost and timing.** It runs first, on the personal Actions quota. A probe costs about 70
minutes, against roughly 600–1,000 for one full build. Its results are recorded in this
design before group 6, and they confirm or revise D3, D4 and D8. It is rerun from that repo
whenever a pinned runner version (D3) is bumped.

**The probe is already written.** It is saved in this change under `probe/`: `probe.sh`, the
workflow at `.github/workflows/runner-probe.yml`, and a README. That directory becomes the
probe repo's contents. Before saving, it was run in full on a maintainer's Mac (macOS 26.7,
Apple M5, bare metal), where every row reported, the microVM boot included. `actionlint` is
clean.

**What the local runs established:**

- `msb doctor` on macOS checks the binaries, `libkrunfw`, reflink cloning and the CPU
  architecture, but **not the hypervisor**. It reports "Host setup is ready." regardless. A
  real boot is the only conclusive check, so the probe runs
  `msb run alpine -- echo microvm-ok`, bounded at 180 s with `perl -e 'alarm …'` because
  macOS has no `timeout`. On the Mac it printed `microvm-ok`.
- microsandbox 0.7.2 ships only `cp310-abi3` wheels, for macOS arm64, manylinux x86_64 and
  aarch64, and Windows amd64 and arm64. So it needs Python ≥ 3.10. The macOS system Python
  is 3.9, which is why the probe workflow installs 3.14 (VISTA's pin) with
  `actions/setup-python`. A uv-created venv has no `pip`, so a local run needs
  `uv venv --seed`.
- msb derives Unix socket paths from `MSB_HOME`, and macOS limits those to 104 bytes. A deep
  `MSB_HOME` fails with "sandbox runtime socket path is too long". The probe uses
  `mktemp -d /tmp/msb.XXXXXX`. This also bears on the real smoke test; see Risks.
- On Windows, `python3` can resolve to the Microsoft Store stub, so the probe uses `python`
  there.

To rerun it locally:
`uv venv --seed --python 3.14 <dir>`, then
`PY=<dir>/bin/python RUNNER_TEMP=<scratch> GITHUB_STEP_SUMMARY=<file> bash probe/probe.sh`.

### Probe results

Run [37051824576](https://github.com/sam-baumann/vista-runner-probe/actions/runs/37051824576)
on 2026-10-02, from `sam-baumann/vista-runner-probe` at its first commit. All three jobs ran
every check; each took under 40 s.

**ubuntu-24.04** (Ubuntu 24.04.5 LTS, kernel 6.17.0-1022-azure, x86_64)

| check | result |
|---|---|
| glibc | 2.39 |
| disk free (`/`, the workspace) | 14 GB of 72 GB |
| `/dev/kvm` before udev rule | `crw-rw---- root kvm` |
| `/dev/kvm` after udev rule | `crw-rw-rw- root kvm`, read+write: yes |
| docker buildx | v0.37.1 |
| Python | 3.14.7 |
| msb | 0.7.2; doctor: "Host setup is ready." |
| microVM boot | `microvm-ok` |
| reach HF / PyPI / nodejs / Electron / gitlab.com / code.ornl.gov | 200 / 200 / 200 / 200 / 301 / 302 |

**macos-15** (macOS 15.7.9, Apple M2 Pro (Virtual), kernel `RELEASE_ARM64_VMAPPLE`)

| check | result |
|---|---|
| `kern.hv_support` | `unknown oid` (exit 1) |
| disk free (`/System/Volumes/Data`) | 43 GiB of 320 GiB |
| Python | 3.14.7 |
| msb | 0.7.2; doctor: "Host setup is ready." |
| microVM boot | fails: `build_microvm: Internal(Vm(VmSetup(VmCreate)))` |
| reach HF / PyPI / nodejs / Electron / gitlab.com / code.ornl.gov | 200 / 200 / 200 / 200 / 301 / 302 |

**windows-2025** (Windows 10.0.26100.33438, Git Bash 5.3.15)

| check | result |
|---|---|
| `LongPathsEnabled` | 1 |
| HypervisorPlatform feature | Enabled |
| disk free (`D:`, the workspace) | 110 GB of 110 GB (`C:` not measured) |
| Python | 3.14.7 |
| msb | 0.7.2; doctor: "Host setup is ready." |
| microVM boot | `microvm-ok` |
| reach HF / PyPI / nodejs / Electron / gitlab.com / code.ornl.gov | 200 / 200 / 200 / 200 / 301 / 302 |

**Against the decisions:**

- **D3, glibc floor:** holds. 24.04 has 2.39, exactly the floor.
- **D3, Linux KVM:** holds. The udev rule makes `/dev/kvm` usable and a microVM boots, so the
  Linux package gets the full smoke test.
- **D4, macOS:** holds. The runner is itself a VM without nested virtualisation, and the boot
  fails even though `doctor` reports ready, which confirms that `doctor` is not a hypervisor
  check on macOS.
- **D4, Windows: did not hold, now revised.** The hosted Windows runner boots a microVM, so
  Windows runs the full smoke test and `--verify-without-sandbox` applies to macOS only. D2,
  D4, D9 and the Risks were revised, and the `.ps1` bypass (the former task 5.3) was
  dropped.
- **D8, disk:** holds on Linux, with about 4 GB of margin against the ~10 GB peak once
  staging is removed early (3.1). macOS and Windows have ample room. Windows' `C:` (where
  `%TEMP%` lives) was not measured.
- **Network: the premise did not hold, now reworded.** code.ornl.gov answered `302`, so the
  runners can reach it, presumably at a sign-in redirect. The maintainer kept the private
  inputs repo (D5), which keeps any ORNL credential and LLM key out of CI. The proposal and
  Context no longer say the host is unreachable.

*Alternative:* learn the same facts from the first full release run. That spends a full
build's minutes per finding, and the workflow is written against guesses.

### D11: One per-job script, so CI's steps run locally

Everything a package job does beyond checkouts, downloads and uploads lives in
`.github/scripts/package.sh`, which is configured entirely through environment variables. The
YAML stays at "check out, fetch inputs, call the script, upload". So:

- a maintainer can run exactly what CI runs, on the Mac, with their own credentials (the
  script uses `AMSC_GIT_TOKEN` only when it is set);
- the only workflow-specific logic left is wiring, which `actionlint` checks statically and
  `act` exercises for the Linux jobs.

`act` runs the `sandbox-image` job and the linux-x86 `package` job in Docker on the Mac. It
can't run macOS or Windows jobs, and Docker on a Mac has no KVM, so the Linux job runs there
with `VERIFY_WITHOUT_SANDBOX=1`. It is a wiring check, optional and manual, and stays out of PR
CI.

Taken together, these are the layers of verification:

1. the scripts, on the Mac (groups 2–5 and 6.2);
2. `actionlint`;
3. `act`, for the Linux jobs;
4. the probe, for runner facts;
5. a manual run;
6. an `-rc` draft;
7. the real-hardware smoke test before publishing.

### D12: Rehearse in a private repo before merging

Before the MR merges on GitLab, the workflow runs for real in a private repo on the
maintainer's own account, `sam-baumann/vista-release-rehearsal`. The branch is pushed there
directly, as that repo's default branch, so `workflow_dispatch` sees the workflow. It gets the
same two secrets as the mirror will. That catches wiring that `actionlint` and `act` can't:
secrets, the inputs checkout over the deploy key, artifact hand-off between jobs, the Windows
job on a real runner, and the `release` job creating and updating a draft.

- **mac is left out of the rehearsal.** Standard `ubuntu-24.04-arm` runners are free only to
  public repos, and the mac job needs the arm64 image they build. A private repo also pays
  macOS's 10× minute multiplier. So the rehearsal builds linux-x86 and win-x86 only. The mac
  job, with its `--verify-without-sandbox` path, is first run on the public mirror (10.2). It
  is the job least covered before then, which the maintainer accepted.
- **The switch.** A repository variable, `RELEASE_PLATFORMS`, holds a JSON list of platform
  keys. Unset, which is how the public mirror runs, it means all three. The `package` matrix
  is built from it, and the `sandbox-image` matrix builds only the architectures those
  platforms need, so no arm64 runner is requested. The rehearsal sets it to
  `["linux-x86","win-x86"]`.
- **Not a way to cut a partial release.** `release` fails before creating anything when the
  variable is set and the repository is public (`github.event.repository.private` is false).
- **Inputs.** The rehearsal uses the real `Genesis-VISTA/vista-build-inputs`, created first
  (9.1) for that reason, with the same read-only deploy key the mirror will hold. So the pinned
  commit is exercised before rollout, and no throwaway inputs repo is needed.
- **Cost.** Private-repo minutes come from the personal quota: Linux at 1×, Windows at 2×.
  The rehearsal's draft release, its rc tag and the repo itself are deleted afterwards.
- **What it can't show.** Organisation Actions policies on Genesis-VISTA (10.1), the arm64
  image build on a native runner, and the mac job.

*Alternative:* rehearse on the public mirror after merging. That is the path the change
exists to make safe, and a broken workflow would then be on the public repo's `main`.

## Risks / Trade-offs

- **[The macOS launcher's first run may need the hypervisor beyond the agent toolset.]**
  For example, the sandbox image import. If so, the smoke test fails even under
  `--verify-without-sandbox`.
  → The probe's `msb doctor` and `kern.hv_support` give a first signal (D10), and the first
  manual run settles it. If it happens, the import is gated by the same variable and reported
  as skipped.
- **[The Windows runner stops booting the sandbox.]** The probe saw it boot once, on one
  image version (D10). A later image, or a different host class behind `windows-2025`, could
  lose the hypervisor platform, and the Windows package job would then fail its smoke test.
  → The failure is loud, not a wrong release. Rerun the probe to confirm. Falling back to
  verification without the sandbox on Windows then needs the `.ps1` bypass that was dropped
  from this change, plus a real-Windows line on the checklist.
- **[msb's 104-byte socket limit on macOS.]** If `MSB_HOME`, or whatever msb derives its
  sockets from, sits too deep, the sandbox fails to start with "socket path is too long". The
  probe hit this locally (D10). On the macOS runner, the smoke test's state directory comes
  from `mktemp -d /tmp/vista-smoke.XXXXXX` (`build_local_package.sh`'s `run_smoke_test`), which
  is short. But the launcher, not the build, decides where msb's home lands.
  → During 5.4 and 10.3, check where the package's msb home resolves on macOS. If it can be
  deep, report it as a launcher precondition, like the Windows path-length checks.
- **[The bypass variable leaks into a real run.]**
  → It is set only inside the smoke test's environment, the launcher prints a notice
  whenever it is honoured, and the spec's researcher scenario pins the refusal.
- **[The real-hardware check before publishing is a manual step that can be forgotten.]**
  → The draft's notes carry an unticked checklist for macOS, and
  `docs/releasing.md` makes ticking it the publishing step.
- **[An archive grows past 2 GiB.]** Today's is 1.7 GB.
  → The `release` job checks sizes and fails with the size named. Science packages are out
  of scope for exactly this reason.
- **[The disk margin is wrong on some runner.]**
  → The `df` logging (D8) shows it. On Linux there's a fallback that deletes the preinstalled
  toolchains.
- **[The pinned inputs commit is lost, for example through a force-push that rewrites the
  inputs repo's history.]**
  → The checkout fails, so the build stops rather than shipping a different corpus.
  `docs/releasing.md` says never to rewrite that repo's history. Branch protection on its
  default branch enforces that.
- **[The deploy key leaks.]**
  → It is read-only and opens one repo, whose contents ship inside every public package
  anyway. Rotate it in the repo's settings.
- **[Mac users who download in a browser hit Gatekeeper.]**
  → This is accepted (D9: ad-hoc signing stays). The notes carry the workaround, and the later curl installer
  avoids the quarantine flag altogether.

## Migration Plan

There is no data or runtime migration. The rollout order:

1. Create the private `sam-baumann/vista-runner-probe`, push the probe workflow to it, run
   it, and record its results here (D10). Revise D3, D4 or D8 if they don't hold.
2. Create the private `Genesis-VISTA/vista-build-inputs` repo, push the first inputs from a
   maintainer's Mac, add a read-only deploy key, and commit the pin.
3. Rehearse the workflow on this branch in the private `sam-baumann/vista-release-rehearsal`,
   on linux and windows (D12), and fix what it turns up. Then delete that repo.
4. Land the script changes, the release workflow, the template and `docs/releasing.md` on
   GitLab. The mirror carries them over.
5. Add two secrets to the mirror: `AMSC_GIT_TOKEN`, a read-only gitlab.com deploy token, and
   `BUILD_INPUTS_DEPLOY_KEY`, the deploy key's private half.
6. When the mirror goes public, so runners are free, do a manual run on `main` and fix
   whatever the runners turn up beyond what the probe predicted.
7. Push `v0.2.0-rc1`. Check that the draft prerelease and its notes are right, run the
   real-hardware smoke test, then delete the rc.
8. Push `v0.2.0` and publish once redistribution is cleared.

**Rollback:** delete the draft or release and the tag. The workflow can be disabled in
GitHub's settings without a commit.
