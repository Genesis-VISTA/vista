**Working notes.**

- Implementation happens in the worktree `.claude/worktrees/github-release-builds`, on branch
  `worktree-github-release-builds`.
- Commit after each task group. Never push or open an MR without the maintainer's explicit
  say-so.
- A full local build (2.1, 3.1, 4.3, 5.4, 6.2, 9.1) needs the following. Tokens come from
  local storage and are never printed:
  - the main checkout's `.env` and `data/` entries symlinked into the worktree;
  - `--payload ~/.vista-build/vista-data --vector-store ~/.vista-build/rag_db`;
  - `VISTA_BUILD_CA_BUNDLE=~/root-ca.pem`, because the office network inspects TLS;
  - gitlab.com access for `amscrot-py` from the macOS keychain.

## 1. Runner probe (runs first, off to the side; nothing lands in the VISTA repo)

- [x] 1.1 Write the probe for the private `sam-baumann/vista-runner-probe`. **Done:** it is saved
  in this change under `probe/` (`probe.sh`, `.github/workflows/runner-probe.yml`, `README.md`),
  was run in full on the maintainer's Mac, and passes `actionlint` (design D10 records what the
  local runs found). It is triggered by
  `workflow_dispatch` only, holds no secrets, and has `permissions: contents: read`. It has one
  job per hosted runner the release builds on: `ubuntu-24.04`, `macos-15` and `windows-2025`.
  `ubuntu-24.04-arm` is left out, because standard arm64 runners are available to public repos
  only; the arm64 image build is exercised natively by `act` on the Mac instead (7.1). The
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

- [ ] 4.1 Take the revision hash from the snapshot the existing stores were embedded with
  (`data/huggingface/hub/models--microsoft--harrier-oss-v1-270m/snapshots/` in the main
  checkout). Add it as `rag_model_revision` next to `rag_model` in
  `mcp_servers/vista_mcp_server/src/vista_mcp_server/config.py`, and pass
  `revision=settings.rag_model_revision` in `rag_mcp.py`. Verify
  `cd mcp_servers/vista_mcp_server && uv run --extra dev pytest` passes.
- [ ] 4.2 Give `build_rag.TextRAG` a `text_model_revision` parameter whose default matches
  4.1, pass it to `SentenceTransformer`, and have `backend/src/vista_backend/utils/indexer.py`
  pass the configured revision through. Verify
  `cd backend && uv run --extra dev pytest -m "not live and not hpc and not sandbox"` passes.
- [ ] 4.3 In `stage_embedding_weights`, read `rag_model_revision` with `rag_model` and download
  with `snapshot_download(model, revision=...)`. When reusing the host cache, fail if the
  pinned snapshot is missing. Verify:
  - a local build stages `snapshots/<pinned-hash>/`;
  - a deliberately wrong hash fails the build with a message naming it.

## 5. Verification without the sandbox

- [ ] 5.1 Add `--verify-without-sandbox` to `scripts/build_local_package.sh`, covering the help
  text, argument parsing and the preflight summary. It exports
  `VISTA_VERIFY_WITHOUT_SANDBOX=1` to the smoke test only, and the build ends with a line
  saying the package was verified without the sandbox. Verify `--check --verify-without-sandbox`
  shows the mode in the summary.
- [ ] 5.2 In `scripts/package_launcher.sh`, turn the `/dev/kvm` refusal into a one-line notice
  when `VISTA_VERIFY_WITHOUT_SANDBOX=1`. Make no other change in behaviour, and update the
  "There is no way to start without it" comment. Verify by reading the diff: the variable is
  consulted only at the refusal.
- [ ] 5.3 In `scripts/smoke_test_package.sh`, under `VISTA_VERIFY_WITHOUT_SANDBOX=1`, report
  the AI-safety retrieval check (and the molten-salt one) through `skip` with a reason. All
  other checks stay as they are. Update the "no opt-out" comment, and end with "all checks
  passed (verified without the sandbox)". Verify by running it on an unpacked package with the
  variable set: retrieval shows as skipped, never ok.
- [ ] 5.4 Run a full local Mac build with and without `--verify-without-sandbox`, as a manual
  check that stays out of PR CI because it needs the sandbox. Verify:
  - without the flag, every check passes as before, retrieval included;
  - with it, retrieval is skipped and the summary says so.

## 6. Release workflow

- [ ] 6.1 Add `.github/build-inputs.env` with `BUILD_INPUTS_COMMIT`, holding a placeholder
  that makes the workflow fail with "no build inputs pinned" until 9.1 sets the real commit.
  Verify that the file's own comment names `docs/releasing.md`.
- [ ] 6.2 Add `.github/scripts/package.sh`, the one script each package job runs (design D11).
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
- [ ] 6.3 Add `.github/workflows/release.yml`:
  - triggers: `push: tags: ['v*']` and `workflow_dispatch`;
  - `permissions: contents: read` by default;
  - a concurrency group per ref that cancels only manual runs;
  - the `sandbox-image` matrix: amd64 on `ubuntu-24.04` and arm64 on `ubuntu-24.04-arm`,
    using buildx and `docker save`, uploaded as a one-day artifact.

  Verify `actionlint` reports no errors (run it through its Docker image or the release
  binary in `$CLAUDE_JOB_DIR/tmp`).
- [ ] 6.4 Add the `package` matrix (linux-x86 on `ubuntu-24.04`, mac-arm64 on `macos-15`,
  win-x86 on `windows-2025` with the Git Bash shell, `fail-fast: false`). Its steps are only:
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
- [ ] 6.5 Add `.github/release-notes.md` (the template from design D9) and a small fill script
  under `.github/scripts/`. Verify by running the script locally against two dummy archives
  with `.sha256` files and reading the rendered notes: the sha256 table, the build-inputs
  commit, the install steps, the quarantine workaround and the unticked real-hardware
  checklist.
- [ ] 6.6 Add the `release` job: `if: startsWith(github.ref, 'refs/tags/v')`, needs `package`,
  `contents: write`. It fails if any archive exceeds 2 GiB, creates the draft (as a
  prerelease when the tag has a suffix) with `gh release create --draft` or updates an
  existing draft, uploads assets with `--clobber`, and sets the rendered notes. Verify
  `actionlint` is clean. The live check is 9.4.

## 7. Local workflow runs with act (optional, manual, outside PR CI)

- [ ] 7.1 Add `.actrc` and a short "Running the workflow locally" section in
  `docs/releasing.md`. Use the `catthehacker/ubuntu:act-24.04` image so glibc matches the
  hosted runner. Supply secrets from a git-ignored `.secrets` file, and pass
  `--container-architecture linux/amd64` on Apple Silicon. Verify
  `act workflow_dispatch -j sandbox-image --matrix arch:amd64` builds and saves the amd64
  image on the Mac. Also run it with `--matrix arch:arm64 --container-architecture linux/arm64`.
  That runs natively on Apple Silicon and stands in for the `ubuntu-24.04-arm` runner the
  private probe can't use.
- [ ] 7.2 Run the linux-x86 `package` job under `act` with `VERIFY_WITHOUT_SANDBOX=1` passed
  through `--env`, since Docker on a Mac has no KVM. Verify it gets through checkout, inputs,
  image download and a complete build. Under amd64 emulation it is slow, so treat it as a
  wiring check, not a timing one. Record anything that behaves differently from the hosted
  runner in `docs/releasing.md`.

## 8. Documentation

- [ ] 8.1 Write `docs/releasing.md`. It covers:
  - cutting a release: tag on GitLab, then the mirror;
  - checking the draft;
  - the real-hardware smoke test on macOS, from the draft asset;
  - publishing;
  - updating the build inputs: index locally with ORNL access and an LLM key, replace
    `vista-data/ai-safety/` and `rag_db/` in a checkout of the private
    `Genesis-VISTA/vista-build-inputs`, commit and push without ever rewriting its history,
    then set `BUILD_INPUTS_COMMIT` in `.github/build-inputs.env`;
  - creating the two secrets: the `AMSC_GIT_TOKEN` gitlab.com deploy token, and the
    `BUILD_INPUTS_DEPLOY_KEY` read-only deploy key;
  - rerunning the runner probe in `sam-baumann/vista-runner-probe` whenever a runner version
    in D3 is bumped.

  Verify every command in it against the scripts.
- [ ] 8.2 Link `docs/releasing.md` from `README.md`'s packaging section and from `AGENTS.md`'s
  build notes, and document `--verify-without-sandbox` in the README's build options. Verify
  the links resolve.
- [ ] 8.3 Run `openspec validate github-release-builds --strict` and `./scripts/ci-local.sh lint`.
  Verify both pass.

## 9. Rollout (manual; needs the public mirror and the maintainer's go-ahead to push)

- [ ] 9.1 Create the private `Genesis-VISTA/vista-build-inputs` repo, with branch protection on
  its default branch that forbids force-pushes. Push `vista-data/ai-safety/` and `rag_db/`
  from the maintainer's Mac, the same inputs the last hand-built package used
  (`~/.vista-build`). Add a read-only deploy key, and commit the real `BUILD_INPUTS_COMMIT`.
  Verify that a fresh clone at that commit passes `build_local_package.sh --check --payload
  … --vector-store …`.
- [ ] 9.2 The maintainer enables Actions on `Genesis-VISTA/vista` and adds the
  `AMSC_GIT_TOKEN` and `BUILD_INPUTS_DEPLOY_KEY` secrets. Verify the release workflow shows
  under the repo's Actions tab, and that no organisation policy blocks hosted runners. The
  probe ran under a personal account, so it couldn't check that.
- [ ] 9.3 Once the mirror is public, so runners are free, make a manual run on `main`. Compare
  disk use, the macOS first run and the Windows full smoke test (sandbox included) against
  the probe results. Fix what turns up, for example by gating the image import on macOS.
  Verify a green run with three archives downloadable from it.
- [ ] 9.4 Push `v0.2.0-rc1` from GitLab. Verify:
  - a draft prerelease appears with three archives, `.sha256` files and correct notes;
  - the full smoke test passes on a real Mac from the draft asset, and the Windows job's own
    smoke test ran with the sandbox.

  Then delete the rc release and its tag.
- [ ] 9.5 Push `v0.2.0`. Publish once the team has cleared redistribution of the AI-safety
  PDFs, the embedding weights and `amscrot-py`. Verify the published release's archives
  download and match their `.sha256` files.
