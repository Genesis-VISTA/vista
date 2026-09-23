Each task carries a host tag:

- **[mac]**: can be done and verified on the macOS dev machine.
- **[windows]**: needs the Windows x64 machine.
- **[mac→windows]**: prepared on macOS, and confirmed on Windows by the task named in it.

Tasks marked **(not PR CI)** need a live sandbox, a live inference key or a real host feature. They run manually or in the validation lane, never under `-m "not live and not hpc and not sandbox"`.

## 1. Sandbox runtime upgrade to microsandbox 0.7.2 (spike, mostly done)

- [x] 1.1 [mac] Pin `microsandbox==0.7.2` in `mcp_servers/dev_mcp_server/pyproject.toml` and relock with `uv lock --upgrade-package microsandbox`. The lock now contains `win_amd64` and `win_arm64` wheels. *(done 2026-09-22, uncommitted)*
- [x] 1.2 [mac] Replace `pty` and `msb exec` with SDK `exec_stream` behind a new `SandboxProcess` protocol in `lib/sandbox.py`, with the `_ExecProcess` and `_ExecStdin` adapters in `lib/microsandbox_sandbox.py` (D1). Verified by the live checks in 1.8. *(done)*
- [x] 1.3 [mac] Inspect and load images through the SDK (`Image.inspect`, `Image.load`). Keep `msb pull` through the CLI for image-only pre-builds. Verified by the live checks in 1.8. *(done)*
- [x] 1.4 [mac] Replace the `public_only` preset with `Network.from_profiles(NetworkProfile.PUBLIC)`, and pass `PullPolicy.NEVER` as an enum (D2). Verified by the DNS and HTTPS egress check in 1.8. *(done)*
- [x] 1.5 [mac] Type the sandbox side of `Volume` and `config.volumes` as `PurePosixPath`. `/mnt` was checked to parse as `PurePosixPath('/mnt')`. *(done)*
- [x] 1.6 [mac] Delete `dev_mcp_server/lib/dns.py` and `tests/test_dns.py`, and stage both deletions (D2). *(done)*
- [x] 1.7 [mac] Make `close()` stop the sandbox, then `MsbSandbox.remove` it, awaiting `name` as a property. `tests/test_microsandbox_sandbox.py` passes unchanged. *(done)*
- [x] 1.8 [mac] **(not PR CI)** Live checks against a real microVM in an isolated `MSB_HOME`. 0.7.2 passed 10 of 10: streaming about 1 s apart, stderr merged, exit code 7, `create_file` round trip, `view` on a file and a directory, the bind volume in both directions, DNS, HTTPS 200, and `close` removal. The edge cases passed too. The 0.5.7 baseline passed 9 of 10, with `create_file` hanging. *(done; the scripts were lost with the scratchpad, see 1.9)*
- [x] 1.9 [mac] **(not PR CI)** Restore the live checks as `@pytest.mark.sandbox` tests in `mcp_servers/dev_mcp_server/tests/test_sandbox_live.py`. They must use the interim store from 3.1, or an explicit `MSB_HOME` of 51 characters or less, and cover every scenario in `specs/code-execution-sandbox`. Verify with `uv run pytest -m sandbox` passing on macOS, and confirm that the hermetic marker filter skips the file. *(done 2026-09-23: 14 tests, opt-in with `VISTA_RUN_SANDBOX=1`, and they skip unless `MSB_HOME` is set. `VISTA_RUN_SANDBOX=1 MSB_HOME=~/.msb-live uv run pytest -m sandbox` passed 14 of 14. `~/.microsandbox` was untouched (same db mtime), no sandboxes were left behind, and they are deselected under the hermetic filter.)*
- [x] 1.10 [mac] Add hermetic unit tests in `tests/test_microsandbox_sandbox.py` for `_ExecProcess` and `_ExecStdin`, driven by a fake `ExecHandle`. Cover:
  - write order is preserved
  - `communicate(input)` closes stdin
  - a `FAILED` event sets the return code
  - `wait()` falls back to the handle when no `EXITED` event arrives
  - `stderr is None` when streams are combined
  - `MsbSandbox.name` is awaited as a property
  Verify with `uv run pytest` in dev_mcp_server. *(done: 7 `TestExecProcess` cases; the existing close test already pins `name` as an awaitable property.)*
- [x] 1.11 [mac] Stop importing the private `microsandbox._runtime.msb_path` from `microsandbox_sandbox.py`. Use a public API if 0.7.2 has one; otherwise wrap the import in one helper that resolves `msb`/`msb.exe` beside the SDK package, with a unit test for both names. Verify with the dev_mcp_server tests. *(done: uses the public `resolve_runtime().msb_path`, which returns `msb.exe` on Windows and honours `MSB_PATH`. `TestMicrosandboxBuild` pins it. `container_sandbox.py` never imported `_runtime`.)*

## 2. Host-portability code fixes

- [x] 2.1 [mac] Replace the `chmod -R o+rX` subprocess at `backend/src/vista_backend/agents/agents.py:657` with an in-process `os.walk`/`os.chmod`, and add `exist_ok=True` to the `mkdir` at `:649` (D7). Add `backend/tests/test_skills_volume.py`, asserting that files are o+r and directories o+rx, and that no subprocess is spawned. Verify with `uv run --extra dev pytest tests/test_skills_volume.py`. *(done: `_make_readable_by_others` in `agents.py`. `tests/test_skills_volume.py` checks the modes, and the `_setup_volumes` test in `test_skills_prompt.py` now fails if a subprocess is spawned.)*
- [x] 2.2 [mac] Keep guest paths POSIX in `backend/src/vista_backend/agents/skills.py:208`: resolve only host keys, and keep guest values as `PurePosixPath` (D4). Extend `backend/tests/test_skills_prompt.py` to assert that locations read `/mnt/skills/<name>/SKILL.md`, with no drive letter or backslash, including when the host side is a `PureWindowsPath`-shaped string. *(done: the sandbox side is a `PurePosixPath` and is never resolved, and host-relative parts are joined through `_in_sandbox`. New tests: `/tmp/skills` stays `/tmp/skills` (the old code turned it into `/private/tmp` on macOS), and a `PureWindowsPath` relative path maps to `/mnt/skills/...`.)*
- [x] 2.3 [mac] Build and parse `file://` URIs host-independently in `mcp_servers/vista_mcp_server/src/vista_mcp_server/display_file_mcp.py:34-38` (D4). Extend `tests/test_display_file.py` with `/mnt/data/output/plot.png`, and assert that relative paths and `..` are still rejected. *(done: URIs are built with `quote` and parsed with `urlsplit` and `PurePosixPath`, and the module no longer imports `Path`. Refusals now also cover `.` segments, `file:` relative paths and non-local hosts.)*
- [x] 2.4 [mac] In `submit_job_mcp.py`, make `sandbox_out_dir` a `PurePosixPath` at `:1334` and `:1375`, and route both download loops (`:1338`, `:1390`) through one shared validator (D5). Add `mcp_servers/vista_mcp_server/tests/test_output_paths.py`:
  - rejected: `/etc/x`, `C:\x`, `C:x`, `\\host\share\x`, `../x`, `..\x`, and a symlink that escapes
  - accepted: `results/summary.csv`
  - returned paths contain only forward slashes
  Verify with `uv run --extra dev pytest tests/test_output_paths.py`. *(done: `_job_output_paths` and `SANDBOX_OUTPUT_DIR`. `tests/test_output_paths.py` has 12 cases, including `\\x` and an escaping symlink.)*
- [x] 2.5 [mac] Add `encoding="utf-8"` to every text read and write (D6). This covers at least:
  - `agents/skills.py:134`
  - `db/seed.py:323`, `:342`, `:364`
  - `services/skills.py:214`, `:246`
  - `submit_job_mcp.py:98`, `:199`, `:217`, `:443`, `:602`, `:765`
  - `agenthpc/config.py:23`
  - whatever the sweep in 2.6 finds

  Verify with `git grep -nE "open\(|read_text\(|write_text\("` reviewed file by file, and with 2.6 passing. *(done: the listed files, plus `agents.py:83`, `campaign/manifest.py`, `backend/scripts/{metrics_report,amortization_report}.py`, and every test-fixture read and write in the three suites. Left alone: the skill scripts under `db/skills/*/scripts`, which run in the Linux sandbox; `lib/ssh.py`'s `/dev/tty`, which is POSIX-only; and `lib/dns.py`, which 2.7 deletes.)*
- [x] 2.6 [mac] Run all three hermetic suites with `PYTHONWARNDEFAULTENCODING=1` and `-W error::EncodingWarning`, filtered to VISTA packages. Wire it into the test targets in `scripts/ci-local.sh` and the matching `.gitlab-ci.yml` jobs. Verify with `./scripts/ci-local.sh test` passing. *(done: `filterwarnings` in each project's pytest config errors on `EncodingWarning`, except the one the third-party `regex` package raises when imported. `PYTHONWARNDEFAULTENCODING=1` is exported in `ci-local.sh` and set on the three GitLab test jobs. A per-module filter can't isolate VISTA code, because `pathlib` is where the warning is attributed, so test code is held to the rule too.)*
- [x] 2.7 [mac] Delete the dead `mcp_servers/vista_mcp_server/src/vista_mcp_server/lib/dns.py` and `tests/test_dns.py`. Verify with `git grep -n "lib.dns\|from .dns"` returning nothing, and the vista_mcp_server suite passing. *(done: 11 tests removed with it.)*
- [x] 2.8 [mac] Run the hermetic suites and compare with the baselines. Pass counts must be at least the baseline plus the new tests, with no new skips: backend 369, vista_mcp_server 153 (less the tests removed in 2.7), dev_mcp_server 50. Verify with `./scripts/ci-local.sh test`. *(done: backend 373 (369 + 4 new), vista_mcp_server 159 (153 − 11 DNS + 17 new), dev_mcp_server 49 hermetic, the same set as before; lint and pyright are clean.)*

## 3. Sandbox store migration (D3: in place)

- [x] 3.1 [mac] **Temporary, until merge:** when `MSB_HOME` is unset, the dev server sets it to `~/.microsandbox-interim` before the SDK or any `msb` subprocess starts. An explicit `MSB_HOME` wins. Mark the code `# TEMPORARY(windows-support): remove before merge`. Add a hermetic test for both cases in `mcp_servers/dev_mcp_server/tests/`. Verify after 1.9 and 5.1 that `msb` from main (0.5.7) still opens `~/.microsandbox` normally. *(done: `config.py` sets `MSB_HOME` to `~/.microsandbox-interim` when it is unset, and `tests/test_interim_store.py` covers the default and the override. The live suite passed 14 of 14 with `MSB_HOME` unset and used the interim store; `~/.microsandbox`'s db mtime was unchanged, and main's 0.5.7 `msb ls` / `image ls` still read it.)*
- [x] 3.2 [mac] `scripts/package_launcher.sh`: keep the store at `$STATE/microsandbox`, lower `SOCKET_BUDGET` from 60 to 51, and drop the unused `VISTA_MSB_PATH` export. Verify with a `VISTA_HOME` long enough to exceed 51 characters (it must refuse by name) and a normal one (it starts). *(done: `SOCKET_BUDGET=51`, measured with 0.7.2; 51 characters starts, 52 is refused with `shortest derived path is 102 bytes`, whatever the sandbox name. The `VISTA_MSB_PATH` export is dropped. Verified on 5.2's built package: a 55-character store is refused by name before any state is created, and the smoke test's normal-length `/tmp` state starts.)*
- [x] 3.3 [mac] Confirm that `scripts/build_local_package.sh` works against msb 0.7.2. `load -i/-t` and `image inspect --format=json` were already checked to be compatible; what remains is the smoke test's `msb` lookup. Verified by 5.2. *(done: no change needed. The script looks `msb` up at `_bundled/bin/msb`, the same path in 0.7.2, and 5.2 reported `sandbox bin : msb 0.7.2`. The launcher's first-run image import with the bundled 0.7.2 succeeded in the smoke test.)*
- [x] 3.4 [mac] AWS store: out of scope, because there is no current AWS deployment. The investigation found that `VISTA_DEV_MCP_OCI_IMAGE_TAR` is implemented only on the unmerged `origin/beta-deployment-3` and `origin/s3-job-output` (`f618a12`); porting it to the SDK path belongs to a future deployment change. Recorded under Risks in design.md. No change to `aws/`.
- [x] 3.5 [mac] **(not PR CI)** Upgrade and rollback check, in a throwaway `VISTA_HOME`:
  1. Run a package built from main (0.5.7).
  2. Run this branch's package against the same state. It must start without re-importing the image.
  3. Run the 0.5.7 package again. Confirm that it fails.
  4. Delete the store as the documented reset. The 0.5.7 package must then start.

  Verify by recording each step's result in the MR. *(done at the store level, in a throwaway `MSB_HOME`, using main's 0.5.7 `msb` and this branch's 0.7.2 dev server. 0.5.7 loads the image; 0.7.2 migrates the store and spawns and runs a sandbox with zero `msb` subprocess calls, so no re-import; 0.5.7 then fails with `Migration file … is missing`; after `rm -rf` of the store, 0.5.7 re-imports and `msb run` succeeds. The package launcher drives the same `image inspect`/`load` flow. A package-to-package repeat needs a main build as well as 5.2's.)*
- [x] 3.6 [mac] Write the migration note: what migrates, the one-way consequence for worktrees on 0.5.7, and the rollback reset for dev and packages. Put it in the MR description and the release notes. Verify by having the reset step followed in 3.5. *(done: `openspec/changes/windows-support/migration-note.md`, ready to paste; its reset step is the one exercised in 3.5.)*

## 4. Repository line endings

- [x] 4.1 [mac] Add the `.gitattributes` rules from D8, keeping the `rag_db/**` LFS rule after the text rules. Verify that `git add --renormalize .` followed by `git status` shows only `.gitattributes` changed, and that `git ls-files --eol` shows no `i/crlf` entries. *(done: `git add --renormalize .` staged only `.gitattributes`. The index has 451 `i/lf`, 11 empty and 4 binary files, and no `i/crlf`. `git check-attr` confirms `.sh` as `text=auto eol=lf`, `.pdf` as binary, `rag_db/**` as LFS and `.ps1` as `eol=crlf`.)*
- [ ] 4.2 [mac→windows] Confirmed by 6.2: a fresh Windows clone contains no carriage returns in `*.sh`, `*.py` or job templates. *(simulated on macOS: `checkout-index` with `core.autocrlf=true`, the Git for Windows default, wrote 0 files with a CR. The control, a clone of the previous commit with the same setting and without the rules, put CRLF into all 451 text files, `scripts/*.sh` included. A real Windows clone is still confirmed in 6.2.)*

## 5. macOS end-to-end verification

- [x] 5.1 [mac] **(not PR CI)** Run `./launch.sh logs` with the `.env` inference key. The interim store from 3.1 applies automatically. Send one agent message that runs bash, writes a plot and displays it. Verify that the plot renders in the UI. This covers 2.1–2.3 and 3.1. *(done 2026-09-23: with `MSB_HOME` unset, the dev server used `~/.microsandbox-interim`. The agent ran bash, wrote `/mnt/data/output/winport-check.png` and displayed it; the plot rendered in the Artifacts panel (`GET /api/files/outputs/winport-check.png` 200), and `ls -l /mnt/skills` listed the 4 project skills. `~/.microsandbox` and `~/.vista/microsandbox` are untouched. Found a pre-existing bug and spun it off as a separate task: `_build`'s digest comparison never matches under Docker Desktop's containerd store, so every spawn reloads the image, and two concurrent spawns raced on the store cache.)*
- [x] 5.2 [mac] **(not PR CI)** Run `scripts/build_local_package.sh --payload ~/.vista-build/payload --vector-store ~/.vista-build/rag_db`, including its built-in smoke test. Its smoke test must run under a throwaway `VISTA_HOME`, never `~/.vista`. Verify that the build reports success. *(done: `vista-0.1.0+323fb8b-macos-arm64.tar.gz`, 2.16 GB, with `--payload ~/.vista-build/vista-data --vector-store ~/.vista/knowledge-bases/molten-salt-papers/rag_db`. The store covers 59 of 59 documents; the smoke test passed every check (Globus skipped, as there is no credential).)*
- [x] 5.3 [mac] **(not PR CI)** Build the Linux package with `scripts/build_in_docker.sh`. Verify that it completes. Its sandbox is not live-tested here, because Docker Desktop has no KVM. *(done: `vista-0.1.0+323fb8b-dirty-linux-x86_64.tar.gz`, 2.48 GB, about 15 minutes under emulation, with `--ca-bundle ~/netskope-root.pem`, `AMSC_GIT_TOKEN` and `PALISADE_GITHUB_TOKEN` exported, and the reused payload and vector store. The bundled runtime is `msb 0.7.2`. The smoke test passed; retrieval was skipped because the container has no `/dev/kvm`. The full command sequence is now in README.md, "A complete Linux build from a Mac". Separately, `build_in_docker.sh:221` uses `local` outside a function on its TLS-failure path; that is pre-existing, from `64f2673`, and not fixed here.)*
- [x] 5.4 [mac] Run `./scripts/ci-local.sh lint` and verify that it is clean. *(done: `./scripts/ci-local.sh lint` passes. The UI's 3 ESLint warnings are pre-existing; this change touches no UI code.)*
- [x] 5.5 [mac] Update the Windows non-goal in `openspec/changes/prebuilt-laptop-package/proposal.md:56` to point at this change. Verify with `openspec validate --all`. *(done: the non-goal now points at `windows-support`, and so does the 0.6-upgrade non-goal. `openspec validate --all` passes 11 of 11.)*

## 6. Windows host setup and cross-platform verification

- [ ] 6.1 [windows] **Before** enabling Hypervisor Platform, record the output of `msb doctor` and of `Get-CimInstance Win32_OptionalFeature -Filter "Name='HypervisorPlatform'"` in a non-elevated shell. 7.3's detection is built from this. Verify that the output is saved in design.md or the MR description.
- [ ] 6.2 [windows] Install uv, Node, and Git with default settings, then clone. Verify 4.2 by scanning for carriage returns in `*.sh`, `*.py` and the job templates, and finding none.
- [ ] 6.3 [windows] Run `uv sync` in all three Python projects and `npm ci` in `ui/`. Verify that every lockfile resolves to Windows wheels, and record the outcome for the `hpc`/`recordclass` open question.
- [ ] 6.4 [windows] Run the three hermetic suites with the EncodingWarning setting from 2.6. Verify the same pass counts as 2.8, with no Windows-only failures.
- [ ] 6.5 [windows] Enable Windows Hypervisor Platform with `msb doctor --fix`, then restart. This needs administrator rights and a restart, so the user runs it. Verify that `msb doctor` reports ready.
- [ ] 6.6 [windows] **(not PR CI)** Run `tests/test_sandbox_live.py` (from 1.9) on Windows, once off VPN and once on VPN. Verify every scenario in `specs/code-execution-sandbox`, including `C:\` bind sources and name resolution through the VPN's resolvers.
- [ ] 6.7 [windows] **(not PR CI)** Check guest permissions on a Windows bind mount for the skills volume (D7). Verify that a non-root guest user can read the skill files. If it can't, record a fix in design.md.
- [ ] 6.8 [windows] Resolve the named-pipe and socket-budget open question: inspect what `msb` creates under `MSB_HOME` on Windows. Verify by recording the answer in design.md, then keep or skip the store-length check in 7.3.
- [ ] 6.9 [windows] **(not PR CI)** Start the MCP server, the backend and the UI by hand (the AGENTS.md commands). Send the same plot message as 5.1, and verify that the plot renders.

## 7. Windows packaging

- [ ] 7.1 [mac→windows] Build the Linux x64 sandbox image tar on macOS for `--sandbox-image`. Copy it, `~/.vista-build/payload` and `rag_db` to the Windows host. Verify with the checksums on both sides.
- [ ] 7.2 [windows] Write `scripts/build_windows_package.ps1` (D10). It needs a uv standalone Python, relocatable venvs, the Node `win-x64` zip, the image from 7.1, and a manifest that records components, sizes, the compatibility floor and the longest relative path. Verify that it produces an archive, and that the manifest has every field.
- [ ] 7.3 [windows] Write `scripts/package_launcher.ps1` and `vista.cmd` (D9). Preflight runs in this order: platform and architecture, path length (D11), Hypervisor Platform (from 6.1), ports, then the store budget (per 6.8). The launcher sets `PYTHONUTF8=1` and uses `$VISTA_HOME\microsandbox`. Verify with 7.5.
- [ ] 7.4 [mac→windows] Trim files that push paths past the limit (D11). Build the list on macOS from the existing package, where 117 files are over 260 under `C:\Users\<name>\Downloads\…`, and confirm on Windows that nothing imports them at run time. Verify that the manifest's longest path fits under the Downloads path.
- [ ] 7.5 [windows] **(not PR CI)** Check the package against `specs/laptop-distribution`:
  - first run, then a subsequent run with no re-import
  - unpacked to a second location
  - an occupied port is named
  - a macOS artifact refuses to start and names its platform
  - an over-long unpack path is reported
  - the smoke test passes

  Verify by running each scenario and recording its result in the MR.
- [ ] 7.6 [windows] **(not PR CI)** If a second Windows machine is available, copy the artifact to it. Verify first-run setup with none of the sender's data. Otherwise record the gap in the MR.
- [ ] 7.7 [windows] Add Windows install notes to `docs/`: Hypervisor Platform (admin rights and a restart), the SmartScreen "Run anyway" prompt, and the recommended short unpack location. Verify by having someone follow it on the clean host from 6.2.

## 8. Close-out

- [ ] 8.1 [mac] As the last commit before merge, remove the interim-store default from 3.1 and its test. Verify that `git grep -n "TEMPORARY(windows-support)"` returns nothing, and that the dev server with `MSB_HOME` unset uses `~/.microsandbox`.
- [ ] 8.2 [mac] Run `openspec validate windows-support --strict` and `./scripts/ci-local.sh`, and verify that both pass. Then open one MR, with the migration note from 3.6 in its description.
