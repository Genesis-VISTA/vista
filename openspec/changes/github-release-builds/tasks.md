**Working notes.**

- Implementation happens in the worktree `.claude/worktrees/github-release-builds`, on branch
  `github-release-builds`.
- Commit after each task group. Never push or open an MR without the maintainer's explicit
  say-so.
- A full local build (2.1, 3.1, 4.3, 5.4, 6.2, 9.1) needs the following. Tokens come from
  local storage and are never printed:
  - the main checkout's `.env` and `data/` entries symlinked into the worktree;
  - `--payload ~/.vista-build/vista-data --vector-store ~/.vista-build/rag_db`;
  - `VISTA_BUILD_CA_BUNDLE=~/root-ca.pem`, because the office network inspects TLS;
  - gitlab.com access for `amscrot-py` from the macOS keychain.

## 1. Runner probe (runs first, off to the side; nothing lands in the VISTA repo)

- [x] 1.1 Write the probe for the private `sam-baumann/vista-runner-probe`. **Done:** it was saved
  in this change under `probe/` (`probe.sh`, `.github/workflows/runner-probe.yml`, `README.md`;
  removed after 9.2, along with the probe repo),
  was run in full on the maintainer's Mac, and passes `actionlint` (design D10 records what the
  local runs found). It is triggered by
  `workflow_dispatch` only, holds no secrets, and has `permissions: contents: read`. It has one
  job per hosted runner the release builds on: `ubuntu-24.04`, `macos-15` and `windows-2025`.
  `ubuntu-24.04-arm` is left out, because standard arm64 runners are available to public repos
  only; the arm64 image build is first run on the public mirror (10.2). The
  workflow reports and builds nothing. Each job prints:
  - `df -h`, or the drive free space on Windows;
  - the OS version, plus `ldd --version` on Linux;
  - on Linux, whether `/dev/kvm` exists and is read/write once the udev rule (the same one
    6.4 uses) is applied;
  - on macOS, `sysctl kern.hv_support`;
  - on all three platforms, using `microsandbox==0.7.2` (the version locked in
    `mcp_servers/dev_mcp_server/uv.lock`) installed under Python 3.14 from
    `actions/setup-python`:
    - `msb --version`;
    - `msb doctor`'s verdict;
    - a real microVM boot, `msb run alpine -- echo microvm-ok`, bounded at 3 minutes. This is
      the decisive check: on macOS, `doctor` doesn't test the hypervisor at all;
  - on `ubuntu-24.04`, that `docker buildx` is present;
  - on Windows, the `LongPathsEnabled` registry value and the Git Bash version;
  - a reachability check (HTTP status only) for huggingface.co, pypi.org, nodejs.org, the
    Electron release downloads, gitlab.com and code.ornl.gov. code.ornl.gov was expected to
    fail. It answered `302` instead (see design "Probe results").

  Every check ends the job green, so a negative answer is reported rather than hiding the
  rest. Verify `actionlint` is clean.
- [x] 1.2 With the maintainer's explicit go-ahead to push, create and run the probe repo. `gh`
  is logged in as `sam-baumann`, and the repo name was free on 2026-10-02.
  1. Copy `openspec/changes/github-release-builds/probe/` to a scratch directory outside the
     repo, then run `git init` and commit there.
  2. `gh repo create sam-baumann/vista-runner-probe --private --source <dir> --push`.
  3. `gh workflow run runner-probe.yml -R sam-baumann/vista-runner-probe`, then
     `gh run watch`.
  4. Read each job's summary (`gh run view <id> --log`, or the run page).

  It costs roughly 70 minutes of the personal Actions quota, macOS's 10× multiplier included,
  and adds no commits to VISTA. Record the three result tables in `design.md` under
  "Probe results". Verify each D-decision still holds against them: the macOS sandbox, the
  Windows `msb doctor`, KVM on Linux, the glibc floor and the disk margins. If one doesn't,
  stop and revise the design before group 6.

## 2. Build identifier

- [x] 2.1 In `scripts/build_local_package.sh`, replace the `0.1.0+<sha>` fallback with
  `git describe --tags --match 'v[0-9]*' --long`, reformatted to `<x.y.z>+<n>.g<sha>`, keeping
  the `-dirty` suffix. Use `0.0.0+g<sha>` when no tag is reachable. Update the "No tags in this
  repo" comment. Verify in a scratch clone outside the repo (`$CLAUDE_JOB_DIR/tmp`):
  - with no tag, `--check` names `vista-0.0.0-<os>-<arch>`;
  - after `git tag v0.2.0` plus three commits, it names `vista-0.2.0-<os>-<arch>` and the
    identifier is `0.2.0+3.g<sha>`;
  - with `VISTA_VERSION=0.3.0`, that value wins.

## 3. Disk headroom

- [x] 3.1 Remove the staging tree after `create_archive` and before `run_smoke_test`, unless
  `--keep-staging` is set, and drop the now-redundant cleanup at the end of the script.
  Verify with a local Mac build:
  - the staging folder under `dist/` is gone while the smoke test runs;
  - with `--keep-staging`, it is still there afterwards.

## 4. Pinned embedding weights

- [x] 4.1 Take the revision hash from the snapshot the existing stores were embedded with
  (`data/huggingface/hub/models--microsoft--harrier-oss-v1-270m/snapshots/` in the main
  checkout). Add it as `rag_model_revision` next to `rag_model` in
  `mcp_servers/vista_mcp_server/src/vista_mcp_server/config.py`, and pass
  `revision=settings.rag_model_revision` in `rag_mcp.py`. Verify
  `cd mcp_servers/vista_mcp_server && uv run --extra dev pytest` passes.
- [x] 4.2 Give `build_rag.TextRAG` a `text_model_revision` parameter whose default matches
  4.1, pass it to `SentenceTransformer`, and have `backend/src/vista_backend/utils/indexer.py`
  pass the configured revision through. Verify
  `cd backend && uv run --extra dev pytest -m "not live and not hpc and not sandbox"` passes.
  **As built:** the backend configures no model, and the indexer passes neither the model nor
  its revision, so it takes `TextRAG`'s defaults as a pair and needed no change. A drift test
  in `test_embedding_model.py` ties both revision defaults together, as it already did for
  the name.
- [x] 4.3 In `stage_embedding_weights`, read `rag_model_revision` with `rag_model` and download
  with `snapshot_download(model, revision=...)`. When reusing the host cache, fail if the
  pinned snapshot is missing. Verify:
  - a local build stages `snapshots/<pinned-hash>/`;
  - a deliberately wrong hash fails the build with a message naming it.

## 5. Verification without the sandbox

- [x] 5.1 Add `--verify-without-sandbox` to `scripts/build_local_package.sh`, covering the help
  text, argument parsing and the preflight summary. It exports
  `VISTA_VERIFY_WITHOUT_SANDBOX=1` to the smoke test only, and the build ends with a line
  saying the package was verified without the sandbox. Verify `--check --verify-without-sandbox`
  shows the mode in the summary.
- [x] 5.2 In `scripts/package_launcher.sh`, turn the `/dev/kvm` refusal into a one-line notice
  when `VISTA_VERIFY_WITHOUT_SANDBOX=1`. Make no other change in behaviour, and update the
  "There is no way to start without it" comment. Verify by reading the diff: the variable is
  consulted only at the refusal.
- [x] 5.3 In `scripts/smoke_test_package.sh`, under `VISTA_VERIFY_WITHOUT_SANDBOX=1`, report
  the AI-safety retrieval check (and the molten-salt one) through `skip` with a reason. All
  other checks stay as they are. Update the "no opt-out" comment, and end with "all checks
  passed (verified without the sandbox)". Verify by running it on an unpacked package with the
  variable set: retrieval shows as skipped, never ok.
- [x] 5.4 Run a full local Mac build with and without `--verify-without-sandbox`, as a manual
  check that stays out of PR CI because it needs the sandbox. Verify:
  - without the flag, every check passes as before, retrieval included;
  - with it, retrieval is skipped and the summary says so.

## 6. Release workflow

- [x] 6.1 Add `.github/build-inputs.env` with `BUILD_INPUTS_COMMIT`, holding a placeholder
  that makes the workflow fail with "no build inputs pinned" until 9.1 sets the real commit.
  Verify that the file's own comment names `docs/releasing.md`.
- [x] 6.2 Add `.github/scripts/package.sh`, the one script each package job runs (design D11).
  It takes everything from environment variables:
  - `BUILD_INPUTS_DIR`, `SANDBOX_IMAGE`, an optional `VISTA_VERSION`, and
    `VERIFY_WITHOUT_SANDBOX`;
  - `AMSC_GIT_TOKEN`, optional, which it applies through a temporary `GIT_CONFIG_GLOBAL` for
    the build only, so a maintainer's own git credentials work when it is unset.

  It prints `df -h` before and after, prints `msb doctor` on Windows, runs
  `build_local_package.sh --payload "$BUILD_INPUTS_DIR/vista-data" --vector-store
  "$BUILD_INPUTS_DIR/rag_db" --sandbox-image "$SANDBOX_IMAGE"` (plus
  `--verify-without-sandbox` when asked), and writes the archive paths to `$GITHUB_OUTPUT`
  when that is set. Verify by running it on the Mac against a local checkout of the inputs
  repo and an exported image tar: it produces the same archive a direct
  `build_local_package.sh` call does.
- [x] 6.3 Add `.github/workflows/release.yml`:
  - triggers: `push: tags: ['v*']` and `workflow_dispatch`;
  - `permissions: contents: read` by default;
  - a concurrency group per ref that cancels only manual runs;
  - the `sandbox-image` matrix: amd64 on `ubuntu-24.04` and arm64 on `ubuntu-24.04-arm`,
    using buildx and `docker save`, uploaded as a one-day artifact. It builds only the
    architectures the platforms in `vars.RELEASE_PLATFORMS` need (design D12; unset means
    all three platforms).

  Verify `actionlint` reports no errors (run it through its Docker image or the release
  binary in `$CLAUDE_JOB_DIR/tmp`).
- [x] 6.4 Add the `package` matrix (linux-x86 on `ubuntu-24.04`, mac-arm64 on `macos-15`,
  win-x86 on `windows-2025` with the Git Bash shell, `fail-fast: false`), narrowed to
  `vars.RELEASE_PLATFORMS` when that is set (D12). Its steps are only:
  - check out VISTA;
  - check out `Genesis-VISTA/vista-build-inputs` at `BUILD_INPUTS_COMMIT` with
    `secrets.BUILD_INPUTS_DEPLOY_KEY`;
  - download the sandbox image for the target;
  - add the udev rule granting `/dev/kvm` on Linux;
  - set `VISTA_VERSION` from the tag on tag runs;
  - run `.github/scripts/package.sh`, with `VERIFY_WITHOUT_SANDBOX=1` on macOS only;
  - upload the archive and its `.sha256` file as artifacts with 7-day retention.

  Verify `actionlint` is clean, and that no build logic sits in the YAML that `package.sh`
  doesn't also run locally.
- [x] 6.5 Add `.github/release-notes.md` (the template from design D9) and a small fill script
  under `.github/scripts/`. Verify by running the script locally against two dummy archives
  with `.sha256` files and reading the rendered notes: the sha256 table, the build-inputs
  commit, the install steps, the quarantine workaround and the unticked real-hardware
  checklist.
- [x] 6.6 Add the `release` job: `if: startsWith(github.ref, 'refs/tags/v')`, needs `package`,
  `contents: write`. It fails if any archive exceeds 2 GiB, creates the draft (as a
  prerelease when the tag has a suffix) with `gh release create --draft` or updates an
  existing draft, uploads assets with `--clobber`, and sets the rendered notes. Before any
  of that, it fails when `vars.RELEASE_PLATFORMS` is set on a public repository (D12). Verify
  `actionlint` is clean. The rehearsal (9.2) runs it for real, and 10.3 is the live check.

  **As built (group 6):**
  - A first `plan` job runs `.github/scripts/plan.sh`. It builds both matrices from
    `RELEASE_PLATFORMS`, because a matrix `include` alone would add back a platform the list
    leaves out. It also fails a placeholder pin before any runner is spent.
  - The release job's work is in `render-notes.sh` and `draft-release.sh`. A re-run keeps a
    maintainer's "What's changed" section. The notes come from the plan's per-platform `verify`
    field rather than a marker file. Substitution uses perl, because `envsubst` is not on macOS.
  - `package.sh` sends `AMSC_GIT_TOKEN` with the username `oauth2`. (An `AMSC_GIT_USER`
    override for a GitLab deploy token was added here, then removed in 9.2 when the maintainer
    chose a fine-grained personal token, which `oauth2` serves.) Its `AMSC_GIT_TOKEN` path could not be
    run locally (no keychain access from the session), so 9.2 is its first real test.
  - 6.2 was verified as the mac job will run it: an image built with `buildx --load` and
    `docker save`, `VERIFY_WITHOUT_SANDBOX=1`, and the same archive name and checks as the
    direct builds in group 5.

## 7. Local workflow runs with act (dropped)

Dropped by the maintainer on 2026-10-02: the rehearsal in group 9 runs the real workflow on
real runners, which is the validation that matters. The group's number is kept so later
references stay stable.

## 8. Documentation

- [x] 8.1 Write `docs/releasing.md`. It covers:
  - cutting a release: tag on GitLab, then the mirror;
  - checking the draft;
  - the real-hardware smoke test on macOS, from the draft asset;
  - publishing;
  - updating the build inputs: index locally with ORNL access and an LLM key, replace
    `vista-data/ai-safety/` and `rag_db/` in a checkout of the private
    `Genesis-VISTA/vista-build-inputs`, commit and push without ever rewriting its history,
    then set `BUILD_INPUTS_COMMIT` in `.github/build-inputs.env`;
  - creating the two secrets: the `AMSC_GIT_TOKEN` gitlab.com fine-grained token, and the
    `BUILD_INPUTS_DEPLOY_KEY` read-only deploy key;
  - checking a new runner with a manual run before relying on it (the probe repo was deleted
    after the rehearsal);
  - rehearsing a workflow change in a private repo with `RELEASE_PLATFORMS` (design D12), and
    what that leaves untested.

  Verify every command in it against the scripts.
- [x] 8.2 Link `docs/releasing.md` from `README.md`'s packaging section and from `AGENTS.md`'s
  build notes, and document `--verify-without-sandbox` in the README's build options. Verify
  the links resolve.
- [x] 8.3 Run `openspec validate github-release-builds --strict` and `./scripts/ci-local.sh lint`.
  Verify both pass.

## 9. Rehearsal in a private repo (before the MR merges; needs the go-ahead to push)

- [x] 9.1 Create the private `Genesis-VISTA/vista-build-inputs` repo. Push `vista-data/ai-safety/`
  and `rag_db/` from the maintainer's Mac: the AI-safety corpus and store every package in
  this change was built and smoke-tested with (`~/.vista`; the `~/.vista-build` stores are
  molten-salt ones). Commit the real `BUILD_INPUTS_COMMIT`. Each consumer gets its own
  read-only credential when its secret is set (9.2, 10.1). Branch protection is unavailable
  (design, Risks), so never rewriting the repo's history is a documented rule only. Deploy keys
  turned out to be disabled for the organisation, so the inputs checkout also accepts a
  fine-grained token, `BUILD_INPUTS_TOKEN` (design D5).
  Verify that a fresh clone at that commit passes `build_local_package.sh --check --payload
  … --vector-store …`. It comes first because the rehearsal uses it (design D12).
- [x] 9.2 Rehearse the workflow in the private `sam-baumann/vista-release-rehearsal` (D12).
  1. Create the repo and push this branch to it as `main`.
  2. Add the `AMSC_GIT_TOKEN` and `BUILD_INPUTS_TOKEN` secrets (the maintainer creates both
     tokens), and set the variable `RELEASE_PLATFORMS` to `["linux-x86","win-x86"]`.
  3. Run `release.yml` by hand on `main`, then push `v0.2.0-rc0` to the rehearsal repo, and
     push it again after a no-op commit, to exercise the draft update and `--clobber`.

  Verify:
  - the manual run is green with two archives as artifacts and creates no release;
  - the tag run creates one draft prerelease with both archives, their `.sha256` files and
    rendered notes naming the pinned inputs commit, and the re-run replaces the assets;
  - the Windows job's smoke test ran with the sandbox, and both jobs print `df -h`;
  - no step needs anything the public mirror won't have.

  Fix what turns up here, on this branch, before the MR merges. Then delete the draft, the tag
  and the repo, and record the minutes used in `docs/releasing.md`.

  As built (2026-10-05):
  - Six runs, about 190 billed minutes. Green jobs: Linux package 10 minutes, Windows 21,
    release 2 to 3. Both packages passed the full smoke test with the sandbox, retrieval
    included, and both jobs printed `df -h`.
  - Fixed on this branch: `setup-uv` pinned to `v10.2.0` (no `v10` tag exists); the MCP app
    build runs vite through `node` (`npx` is `npx.cmd` on Windows); the checksum falls back to
    `sha256sum` (Git Bash has no `shasum`); the Linux package job frees disk first (D8); and
    the smoke test prints `setup.log` when the launcher fails.
  - The tag run built Linux only (`RELEASE_PLATFORMS=["linux-x86"]`), since Windows was
    already proven. It made one draft prerelease, `VISTA 0.2.0-rc0`, with
    `vista-0.2.0-rc0-linux-x86.tar.gz`, its `.sha256` (matching the notes) and fully rendered
    notes naming inputs `c3c71e94`. The re-push was replaced by rerunning only the release job
    against the same artifacts (`gh run rerun --job`): it edited the same draft, kept a
    hand-added "What's changed" line, and replaced both assets.
  - Afterwards the maintainer deleted the rehearsal and probe repos, so the draft and tag went
    with them, and the probe was removed from this change.

## 10. Rollout (manual; needs the public mirror and the maintainer's go-ahead to push)

- [ ] 10.1 The maintainer enables Actions on `Genesis-VISTA/vista` and adds the
  `AMSC_GIT_TOKEN` secret and an inputs credential: `BUILD_INPUTS_DEPLOY_KEY` if an
  organisation owner has allowed deploy keys by then, otherwise `BUILD_INPUTS_TOKEN`. Verify the release workflow shows
  under the repo's Actions tab, and that no organisation policy blocks hosted runners. The
  probe ran under a personal account, so it couldn't check that.
- [ ] 10.2 Once the mirror is public, so runners are free, make a manual run on `main`. Compare
  disk use, the macOS first run and the Windows full smoke test (sandbox included) against
  the probe results. Fix what turns up, for example by gating the image import on macOS.
  This is the mac job's first real run. Verify a green run with three archives downloadable
  from it.
- [ ] 10.3 Push `v0.2.0-rc1` from GitLab. Verify:
  - a draft prerelease appears with three archives, `.sha256` files and correct notes;
  - the full smoke test passes on a real Mac from the draft asset, and the Windows job's own
    smoke test ran with the sandbox.

  Then delete the rc release and its tag.
- [ ] 10.4 Push `v0.2.0`. Publish once the team has cleared redistribution of the AI-safety
  PDFs, the embedding weights and `amscrot-py`. Verify the published release's archives
  download and match their `.sha256` files.

## 11. One-line install (follow-up MR, branch `release-install-scripts`)

A user installs and starts VISTA with one command instead of the release notes' per-platform
steps:

```bash
curl -fsSL https://github.com/Genesis-VISTA/vista/releases/latest/download/install.sh | bash
```
```powershell
powershell -ExecutionPolicy Bypass -c "irm https://github.com/Genesis-VISTA/vista/releases/latest/download/install.ps1 | iex"
```

The scripts are attached to every release as assets with fixed names, so `releases/latest/download/`
always serves the newest published release's own script (GitHub skips drafts and prereleases
there), and a prerelease is installed from `releases/download/<tag>/install.sh`. Each script
has its release's version written in at release time, so it needs no GitHub API call and no
JSON parsing. The packages are unchanged: the scripts only do what the release notes tell a
person to do.

- [ ] 11.1 Update the proposal, design (a new decision on the installer) and the
  `release-builds` spec delta (requirements: one command per platform; verify before
  unpacking; a re-run of the same version starts the installed copy without downloading;
  state in `VISTA_HOME` survives an upgrade).
- [ ] 11.2 `scripts/install.sh` for macOS and Linux, run as `curl … | bash`:
  - the whole body is inside a function called on the last line, so a truncated download runs
    nothing; `set -euo pipefail`;
  - detect the platform from `uname -s`/`uname -m` (`mac-arm64`, `linux-x86`) and refuse any
    other with the list of what is offered, before downloading;
  - on Linux, check `/dev/kvm` is usable and there is a desktop session first, reusing the
    launcher's wording, so a 1.7 GB download is not wasted on a machine that cannot run it;
  - check free disk (about 7 GB: the archive, the 4.6 GB unpacked package, and first-run
    state) where it installs;
  - download the archive and its `.sha256` with `curl -fL` into a temporary directory, verify
    with `shasum -a 256 -c` or `sha256sum -c`, and stop on a mismatch;
  - install into `~/.local/share/vista/<version>/` (overridable with `VISTA_INSTALL_DIR`),
    unpack into a sibling temporary directory and rename it into place, so an interrupted
    install never leaves a half-unpacked package where the next run would find it;
  - link `~/.local/bin/vista` to the launcher, and say so (and how to add it to `PATH` when it
    is not on it);
  - remove the previous version's package after the new one is in place. State lives in
    `VISTA_HOME` (`~/.vista`), outside the install, so it is untouched;
  - start the launcher with `exec … < /dev/tty`, since stdin is the pipe the script came from;
    `--no-launch` installs only;
  - `--version <tag>` installs a specific release, including a prerelease.
- [ ] 11.3 `scripts/install.ps1` for Windows, run with `irm … | iex`, the same steps in
  PowerShell:
  - install into `%LOCALAPPDATA%\VISTA\app`, with no version in the folder name: the package
    leaves only 62 characters for the folder it is unpacked into (176-character longest path,
    260 limit), and `C:\Users\<name>\AppData\Local\VISTA\vista-0.2.0-rc1-win-x86` would use
    nearly all of it. Check the length before downloading, using the launcher's own rule;
  - verify with `Get-FileHash`, unpack with Windows' own `tar.exe` (bsdtar reads zip, and is
    far faster than `Expand-Archive` on a package this size);
  - add a Start menu shortcut to `vista.cmd`, so the next start needs no terminal;
  - start `vista.cmd`, or stop with `-NoLaunch`.
- [ ] 11.4 Release wiring: `render-notes.sh` (or a sibling) writes the version into both
  scripts, `draft-release.sh` uploads them with the archives, and the size check ignores them.
- [ ] 11.5 Rewrite `.github/release-notes.md` around the two commands: one line per platform,
  then the archive table, with the current manual steps kept in a collapsed `<details>` section
  for anyone who cannot pipe a script into a shell. Keep the macOS quarantine note there only.
- [ ] 11.6 Tests:
  - hermetic, in GitLab PR CI: run each script against a tiny fake package served from a
    `file://` base URL (`VISTA_INSTALL_BASE_URL`), covering a fresh install, a re-run of the
    same version (no download), an upgrade (state kept, old package removed), a checksum
    mismatch (stops, installs nothing) and an unsupported platform. `shellcheck` and
    PSScriptAnalyzer on the scripts;
  - in the release workflow's package job, after the smoke test: run the platform's script
    against the job's own archive over `file://` with `--no-launch`, and check the installed
    `VERSION`. This catches a script that no longer matches the packages, on every run.
- [ ] 11.7 Update the README's "Running a prebuilt package" and `docs/releasing.md` to lead with
  the one-line install.
- [ ] 11.8 Verify on the mirror after merge: a prerelease tag's `install.sh` installs and starts
  VISTA on a real Mac and on Linux, and `install.ps1` on Windows, each from its
  `releases/download/<tag>/` URL. `releases/latest/download/` is first exercised by `v0.2.0`.
