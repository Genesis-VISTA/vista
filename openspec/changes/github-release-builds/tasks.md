## 1. Build identifier

- [ ] 1.1 In `scripts/build_local_package.sh`, replace the `0.1.0+<sha>` fallback with
  `git describe --tags --match 'v[0-9]*' --long`, reformatted to `<x.y.z>+<n>.g<sha>`, keeping
  the `-dirty` suffix. Use `0.0.0+g<sha>` when no tag is reachable. Update the "No tags in this
  repo" comment. Verify in a scratch clone outside the repo (`$CLAUDE_JOB_DIR/tmp`):
  - with no tag, `--check` names `vista-0.0.0-<os>-<arch>`;
  - after `git tag v0.2.0` plus three commits, it names `vista-0.2.0-<os>-<arch>` and the
    identifier is `0.2.0+3.g<sha>`;
  - with `VISTA_VERSION=0.3.0`, that value wins.

## 2. Disk headroom

- [ ] 2.1 Remove the staging tree after `create_archive` and before `run_smoke_test`, unless
  `--keep-staging` is set, and drop the now-redundant cleanup at the end of the script.
  Verify with a local Mac build:
  - the staging folder under `dist/` is gone while the smoke test runs;
  - with `--keep-staging`, it is still there afterwards.

## 3. Pinned embedding weights

- [ ] 3.1 Take the revision hash from the snapshot the existing stores were embedded with
  (`data/huggingface/hub/models--microsoft--harrier-oss-v1-270m/snapshots/` in the main
  checkout). Add it as `rag_model_revision` next to `rag_model` in
  `mcp_servers/vista_mcp_server/src/vista_mcp_server/config.py`, and pass
  `revision=settings.rag_model_revision` in `rag_mcp.py`. Verify
  `cd mcp_servers/vista_mcp_server && uv run --extra dev pytest` passes.
- [ ] 3.2 Give `build_rag.TextRAG` a `text_model_revision` parameter whose default matches
  3.1, pass it to `SentenceTransformer`, and have `backend/src/vista_backend/utils/indexer.py`
  pass the configured revision through. Verify
  `cd backend && uv run --extra dev pytest -m "not live and not hpc and not sandbox"` passes.
- [ ] 3.3 In `stage_embedding_weights`, read `rag_model_revision` with `rag_model` and download
  with `snapshot_download(model, revision=...)`. When reusing the host cache, fail if the
  pinned snapshot is missing. Verify:
  - a local build stages `snapshots/<pinned-hash>/`;
  - a deliberately wrong hash fails the build with a message naming it.

## 4. Verification without the sandbox

- [ ] 4.1 Add `--verify-without-sandbox` to `scripts/build_local_package.sh`, covering the help
  text, argument parsing and the preflight summary. It exports
  `VISTA_VERIFY_WITHOUT_SANDBOX=1` to the smoke test only, and the build ends with a line
  saying the package was verified without the sandbox. Verify `--check --verify-without-sandbox`
  shows the mode in the summary.
- [ ] 4.2 In `scripts/package_launcher.sh`, turn the `/dev/kvm` refusal into a one-line notice
  when `VISTA_VERIFY_WITHOUT_SANDBOX=1`. Make no other change in behaviour, and update the
  "There is no way to start without it" comment. Verify by reading the diff: the variable is
  consulted only at the refusal.
- [ ] 4.3 Make the same change in `scripts/package_launcher.ps1` for the `msb doctor` refusal.
  Verify the same way, and on the Windows runner in 7.3.
- [ ] 4.4 In `scripts/smoke_test_package.sh`, under `VISTA_VERIFY_WITHOUT_SANDBOX=1`, report
  the AI-safety retrieval check (and the molten-salt one) through `skip` with a reason. All
  other checks stay as they are. Update the "no opt-out" comment, and end with "all checks
  passed (verified without the sandbox)". Verify by running it on an unpacked package with the
  variable set: retrieval shows as skipped, never ok.
- [ ] 4.5 Run a full local Mac build with and without `--verify-without-sandbox`, as a manual
  check that stays out of PR CI because it needs the sandbox. Verify:
  - without the flag, every check passes as before, retrieval included;
  - with it, retrieval is skipped and the summary says so.

## 5. Release workflow

- [ ] 5.1 Add `.github/build-inputs.env` with `BUILD_INPUTS_VERSION` and
  `BUILD_INPUTS_SHA256`, holding placeholders that make the workflow fail with "no build inputs
  pinned" until 7.1 sets real values. Verify that the file's own comment names `docs/releasing.md`.
- [ ] 5.2 Add `.github/workflows/release.yml`:
  - triggers: `push: tags: ['v*']` and `workflow_dispatch`;
  - `permissions: contents: read` by default;
  - a concurrency group per ref that cancels only manual runs;
  - the `sandbox-image` matrix: amd64 on `ubuntu-24.04` and arm64 on `ubuntu-24.04-arm`,
    using buildx and `docker save`, uploaded as a one-day artifact.

  Verify `actionlint` reports no errors (run it through its Docker image or the release
  binary in `$CLAUDE_JOB_DIR/tmp`).
- [ ] 5.3 Add the `package` matrix (linux-x86 on `ubuntu-24.04`, mac-arm64 on `macos-15`,
  win-x86 on `windows-2025` with the Git Bash shell, `fail-fast: false`). Each job:
  - sets `VISTA_VERSION` from the tag on tag runs;
  - adds the udev rule granting `/dev/kvm` on Linux;
  - fetches the bundle and checks its sha256 against the pin before anything else;
  - configures `amscrot-py` access from `secrets.AMSC_GIT_TOKEN`;
  - logs `df -h` before and after the build;
  - logs `msb doctor` on Windows;
  - runs `build_local_package.sh --payload --vector-store --sandbox-image`, adding
    `--verify-without-sandbox` on macOS and Windows;
  - uploads the archive and its `.sha256` file as artifacts with 7-day retention.

  Verify `actionlint` is clean.
- [ ] 5.4 Add `.github/release-notes.md` (the template from design D9) and a small fill script
  under `.github/scripts/`. Verify by running the script locally against two dummy archives
  with `.sha256` files and reading the rendered notes: the sha256 table, the bundle version,
  the install steps, the quarantine workaround and the unticked real-hardware checklist.
- [ ] 5.5 Add the `release` job: `if: startsWith(github.ref, 'refs/tags/v')`, needs `package`,
  `contents: write`. It fails if any archive exceeds 2 GiB, creates the draft (as a
  prerelease when the tag has a suffix) with `gh release create --draft` or updates an
  existing draft, uploads assets with `--clobber`, and sets the rendered notes. Verify
  `actionlint` is clean. The live check is 7.4.

## 6. Documentation

- [ ] 6.1 Write `docs/releasing.md`. It covers:
  - cutting a release: tag on GitLab, then the mirror;
  - checking the draft;
  - the real-hardware smoke test on macOS and Windows, from the draft asset;
  - publishing;
  - refreshing the corpus bundle: index locally with ORNL access and an LLM key, pack
    `vista-data/ai-safety/` and `rag_db/`, publish on `Genesis-VISTA/vista-build-inputs`,
    update `.github/build-inputs.env`;
  - creating the `AMSC_GIT_TOKEN` deploy token.

  Verify every command in it against the scripts.
- [ ] 6.2 Link `docs/releasing.md` from `README.md`'s packaging section and from `AGENTS.md`'s
  build notes, and document `--verify-without-sandbox` in the README's build options. Verify
  the links resolve.
- [ ] 6.3 Run `openspec validate github-release-builds --strict` and `./scripts/ci-local.sh lint`.
  Verify both pass.

## 7. Rollout (manual; needs the public mirror and the maintainer's go-ahead to push)

- [ ] 7.1 Build and publish the first corpus bundle from the maintainer's Mac. Create the
  `Genesis-VISTA/vista-build-inputs` repo, attach the bundle as a release asset, and commit
  the real version and sha256 to `.github/build-inputs.env`. Verify that downloading the
  asset and running `shasum -a 256` matches the pin.
- [ ] 7.2 The maintainer enables Actions on `Genesis-VISTA/vista` and adds the `AMSC_GIT_TOKEN`
  secret as a read-only deploy token. Verify the workflow shows under the repo's Actions tab.
- [ ] 7.3 Make a manual run on `main`. Record the disk margins, whether the macOS first run
  needs the hypervisor beyond the toolset, and what `msb doctor` says on Windows. Fix what
  turns up, for example by gating the image import or dropping the flag on Windows. Verify a
  green run with three archives downloadable from it.
- [ ] 7.4 Push `v0.2.0-rc1` from GitLab. Verify:
  - a draft prerelease appears with three archives, `.sha256` files and correct notes;
  - the full smoke test passes on a real Mac and a real Windows machine from the draft assets.

  Then delete the rc release and its tag.
- [ ] 7.5 Push `v0.2.0`. Publish once the team has cleared redistribution of the AI-safety
  PDFs, the embedding weights and `amscrot-py`. Verify the published release's archives
  download and match their `.sha256` files.
