## Context

See [proposal.md](proposal.md) for the motivation. The facts that shape the approach:

- **The microsandbox 0.7.2 spike is done** and uncommitted on `windows-support`.
  - The dev server now runs commands through the SDK instead of `pty` and the `msb exec` CLI.
  - Live checks on macOS: 10 of 10 pass on 0.7.2. On 0.5.7, 9 of 10 pass, and `create_file` hangs because stdin was the pty slave.
  - Edge cases also pass on 0.7.2: Python output streaming, a missing program, separate byte-exact streams, and cwd and env.
  - Hermetic tests: 50 pass, down from 61; the 11 missing are the removed DNS tests.
  - The live-check scripts were in the session scratchpad, which has since been cleared. Task 1.9 restores them as marker-gated tests.
- **The 0.7 store migration is one-way.** Once 0.7.2 opens a 0.5.7 store, 0.5.7 fails with `Migration file … is missing`. Three stores exist today:
  - Dev checkouts share `~/.microsandbox`, the default, because nothing sets `MSB_HOME`.
  - Packages use `$VISTA_HOME/microsandbox`, set by `package_launcher.sh`.
  - AWS uses `/data/msb`, set in `aws/Dockerfile.server`.
- **0.7's socket-path limit** needs `MSB_HOME` at about 53 characters or less on macOS. The launcher's `SOCKET_BUDGET` is 60.
- **Windows specifics:**
  - The runtime needs the Windows Hypervisor Platform feature, not `VirtualMachinePlatform`. Enabling it needs admin rights and a restart, and `msb doctor --fix` can do it.
  - Upstream labels Windows support as preview.
  - Python 3.14 on Windows defaults to cp1252 for text files.
  - `Path` is `WindowsPath`, so `/mnt` becomes `\mnt`.
  - Git for Windows checks files out with CRLF by default.
  - The current macOS package unpacked under `C:\Users\<name>\Downloads\…` puts 117 files past the 260-character path limit.
- **SDK behavior differs from its stub:** `MsbSandbox.name` is an awaitable property at runtime, although the `.pyi` stub declares `async def name()`.

## Goals / Non-Goals

**Goals:**
- Finish every cross-platform fix on macOS, where it can be tested against the existing Mac and Linux builds, before the Windows session.
- Leave the Windows session only the work that needs Windows: the launcher, the build, the preflight checks and the live verification.
- Existing macOS and Linux checkouts, packages and deployments keep working, including across rollback.

**Non-Goals:**
- Replacing the bash launchers on macOS and Linux. The PowerShell launcher is added alongside them.
- Upstream fixes to microsandbox. Windows bugs found there are worked around and reported.
- A live sandbox on Linux in this change. Docker Desktop on the Mac has no KVM, so Linux gets hermetic tests only.

## Decisions

### D1. Run commands through the SDK's streaming exec, behind a `SandboxProcess` protocol *(done)*

`Sandbox.exec` now returns a `SandboxProcess` protocol that exposes `stdin`, `stdout`, `stderr`, `returncode`, `wait` and `communicate`. Both the container backend and the microsandbox backend satisfy it, so `server.py` does not change shape.

The microsandbox backend adapts an `ExecHandle`:
- Events are pumped into `asyncio.StreamReader`s.
- Stdin writes are chained onto one task, because `ExecSink` is async-only.
- A guest tty is requested only for combined-stream execs. It makes programs line-buffer, which gives the incremental output `run_bash` streams. Separate-stream execs must stay byte-exact, so they get no tty.
- `run_bash` keeps its `\r\n` → `\n` replacement, since the guest terminal translates `\n`.

Alternatives considered:
- **ConPTY or `pywinpty` on Windows.** That would mean two code paths, and the stdin bug would remain.
- **Keep the `msb exec` CLI with pipes.** That loses line-buffered streaming.

### D2. Leave DNS to microsandbox *(done)*

msb 0.7 reads the host's resolver configuration itself: SCDynamicStore on macOS, resolv.conf on Linux, and the DNS Client on Windows, which is NRPT and VPN-aware. Passing explicit nameservers would bypass that, so `dev_mcp_server/lib/dns.py` is deleted. Egress uses `Network.from_profiles(NetworkProfile.PUBLIC)`, which replaces the removed `public_only` preset. `vista_mcp_server/lib/dns.py` is already dead code (only its own test imports it), and it is removed as well.

### D3. Migrate the sandbox store in place

Every consumer keeps its current store path: `~/.microsandbox` for dev, `$VISTA_HOME/microsandbox` for packages, `/data/msb` for AWS. 0.7 migrates each one the first time it opens it. A migration test on a copy of `~/.microsandbox` succeeded. Task 3.5 confirms that the images survive, so nothing is re-imported or rebuilt.

The cost is rollback. Once migrated, a store no longer opens under 0.5.7 (`Migration file … is missing`). The reset is to delete the store directory, after which the old version re-imports or rebuilds its image on next start. The consequences:

- **Dev worktrees are one-way from the first run.** The first time any worktree runs on 0.7, `~/.microsandbox` migrates, and every worktree still on 0.5.7 (main included, until this merges) fails to start its sandbox until it rebases or resets. To avoid that before merge, the branch carries a temporary default: when `MSB_HOME` is unset, the dev server sets it to a separate interim store, `~/.microsandbox-interim`, before the SDK or any `msb` subprocess starts. Every live run on the branch then leaves `~/.microsandbox` alone without anyone having to remember an export. The default is removed as the last commit before merge (task 8.1), and from then on the permanent in-place migration applies. The interim store can be deleted after merge.
- **Packages:** upgrading needs no action. Rolling back means deleting `$VISTA_HOME/microsandbox`, which the release notes say.
- **AWS:** the first 0.7 deploy migrates `/data/msb`. Rolling back that deploy needs the same reset on the data volume.

Why this and not a separate store: the store path stays the same everywhere, there is no roughly 855 MB duplicate per consumer, and there is no stale directory to clean up. Chosen by the project owner on 2026-09-23 over a version-scoped store (`~/.microsandbox-0.7` and so on), which would have kept rollback reset-free.

Alternatives considered:
- **A version-scoped store everywhere.** Rollback needs no reset, but every consumer imports the image again and keeps an old directory around.
- **A separate store for dev only.** Worktrees coexist, but that is the only problem it solves.

### D4. Type sandbox-side paths as `PurePosixPath`

Every path that names a location inside the guest is a `PurePosixPath` or a `str`. It is never `Path` and never `.resolve()`d on the host.
- **Volume config:** `Volume` and `config.volumes` *(done)*.
- **`skills.py:208`:** only host keys may be resolved. Guest values stay POSIX.
- **`display_file_mcp.py`:** build and parse `file://` URIs with `urllib.parse` and `PurePosixPath` instead of `Path.as_uri` and `Path.from_uri`, which are host-dependent.
- **`submit_job_mcp.py` job-output paths:** `sandbox_out_dir` becomes a `PurePosixPath`.

### D5. Contain downloads with one shared validator

Both job-output functions (`submit_job_mcp.py:1338` and `:1390`) call one helper. The helper rejects a name when either of these holds:
- it is absolute or anchored under either `PurePosixPath` or `PureWindowsPath`. That covers `/x`, `C:\x`, `C:x` and `\\host\share`.
- `..` appears in its parts under either parse.

It then asserts that `(local_out_dir / name).resolve()` is still relative to `local_out_dir.resolve()`. That check also covers symlinks, and it works the same on every host, so macOS tests prove the Windows behavior.

### D6. Make every text read and write UTF-8, and enforce it in tests

- Pass `encoding="utf-8"` explicitly on every `open`, `read_text` and `write_text`.
- Enforce it by running the hermetic suites with `PYTHONWARNDEFAULTENCODING=1 -W error::EncodingWarning`, filtered to VISTA's own packages.
- The launchers also set `PYTHONUTF8=1`, as a backstop for third-party code. The explicit encodings are the real fix, and the tests check for them without relying on the backstop.

Alternative considered: `PYTHONUTF8=1` alone. It does not help anyone who starts a service outside the launchers, such as dev runs or tests.

### D7. Set skill permissions in-process instead of running `chmod`

`agents.py:657` replaces `chmod -R o+rX` with an `os.walk` that adds read permission to files, and read and execute permission to directories, through `os.chmod`.
- `agents.py:649` gains `exist_ok=True`.
- On Windows, `os.chmod` only toggles the read-only flag. Whether guest permissions on a Windows bind mount come out readable for a non-root sandbox user has to be checked on Windows (task 7.6).

### D8. Add `.gitattributes` line-ending rules

The file declares, in order:
- `* text=auto eol=lf`
- explicit `binary` rules for images, archives, `.pt`, `.tar` and other binary formats
- the existing LFS rule for `rag_db/**`, which must come after the text rules so it wins
- `*.bat` and `*.cmd` as `eol=crlf`; `*.ps1` as `eol=crlf`

Why this is safe for existing checkouts: `text=auto` only affects files Git detects as text. Every committed text file is already LF in the index, and LF is also what macOS and Linux check out, so no working file changes. Task 4.1 verifies this with `git add --renormalize .`, which must stage nothing except `.gitattributes`.

### D9. Add a PowerShell launcher alongside the bash one

`scripts/package_launcher.ps1` mirrors `package_launcher.sh` step for step, and `vista.cmd` lets it be double-clicked or run from `cmd`. Bash and job control have these equivalents:
- `Start-Process` and job objects, so child processes die with the launcher
- `System.Net.Sockets.TcpClient` for port probes
- `Resolve-Path` for `realpath`

Preflight order:
1. platform and architecture
2. path length (D11)
3. Hypervisor Platform
4. ports
5. store path budget

Hypervisor Platform is detected without admin rights through `msb doctor` output, falling back to CIM `Win32_OptionalFeature`. `Get-WindowsOptionalFeature` needs elevation, so it is not used.

The launcher sets `PYTHONUTF8=1` and finds `msb.exe`. The unused `VISTA_MSB_PATH` export is dropped from both launchers.

### D10. Build Windows packages on a Windows host

`build_local_package.sh` assumes POSIX layouts throughout: `.venv/bin`, symlinks, `sh` wrappers, `rsync` and xattrs. Cross-building Windows from macOS would also still need a Windows environment for the mandatory smoke test. So the Windows target gets its own entry point, `scripts/build_windows_package.ps1`:
- The sandbox image is Linux x64 either way. It is built on any host and passed in with `--sandbox-image`, so the Windows host needs no Docker.
- The payload and `rag_db` are copied from `~/.vista-build`.
- Python comes from uv's standalone interpreter, and venvs are made relocatable with `uv venv --relocatable`, which uses `Scripts\` and a relative `home`.
- Node comes from the `win-x64` zip.
- The platform-neutral steps (manifest, compatibility record, smoke test) are kept structurally identical to the bash script, so the two can be diffed by eye.

The laptop-distribution delta records this exception to cross-building.

### D11. Check path length in preflight, and trim the package

- The build records the package's longest relative path in its manifest.
- The launcher adds its unpack location's length and compares the total with 260, unless `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled` is 1. That key is readable without admin rights.
- On failure, the launcher suggests a short location such as `C:\vista`, or enabling long paths.
- The build also drops the files that push past the limit when nothing imports them at run time, for example JupyterLab extension assets in the MCP server venv. That keeps typical unpack locations under the limit.

## Risks / Trade-offs

- **[microsandbox on Windows is preview]** → The adapter stays small and isolated behind `SandboxProcess`. Windows-only failures are reproduced with a minimal script and reported upstream, and the container backend remains a fallback for development.
- **[Hypervisor Platform needs admin and a restart; managed ORNL laptops may forbid it]** → Preflight names the requirement before anything starts (D9). Raise it with IT before the Windows build work, since no code can remove it.
- **[Unsigned `msb.exe` and `python.exe` trip SmartScreen or Defender]** → The first-run documentation covers "More info → Run anyway". Signing is a non-goal and stays tracked here.
- **[The SDK stub and the runtime disagree (`name`)]** → A unit test pins the awaitable-property behavior, so an SDK change that makes `name` a method fails loudly.
- **[Rollback needs a manual store reset]** → The release notes and the Windows install notes give the one reset step (delete the store directory). Task 3.5 checks that the step actually restores the old version.
- **[One live run on this branch against the shared `~/.microsandbox` breaks every 0.5.7 worktree]** → The branch's interim-store default (D3) until merge, then announce the migration when the change merges.
- **[AWS sets `VISTA_DEV_MCP_OCI_IMAGE_TAR`, but no code reads it]** → Check how the AWS image actually reaches the store, and confirm that `/data/msb` migrates cleanly on the first 0.7 deploy (task 3.4).
- **[Linux live sandbox is untested in this change]** → Hermetic tests only. Run the live sandbox tests (task 1.9) on a Linux host with KVM before release.
- **[The resolv.conf that microsandbox writes has mode 0700]** → This is an existing TODO. The sandbox image stays root until it is fixed upstream.

## Migration Plan

1. Until merge, the branch's dev server defaults to `~/.microsandbox-interim` (D3). Packages built from the branch are tested only under a throwaway `VISTA_HOME`, never `~/.vista`.
2. Remove the interim default as the last commit, then merge to main in one MR, and tell developers that the first run migrates `~/.microsandbox`. Any worktree still on 0.5.7 must rebase, or delete `~/.microsandbox` and rebuild on its next start.
3. Packages: the next release migrates `$VISTA_HOME/microsandbox` on first run. The release notes give the rollback reset.
4. AWS: the first 0.7 deploy migrates `/data/msb`. Rolling back redeploys the previous image, and then needs `/data/msb` deleted.

## Open Questions

- Does `msb` on Windows use named pipes, or anything else bound by a path budget? That decides whether the store-length check applies on Windows. It only changes one preflight check.
- The optional `hpc` extra pulls in `recordclass`, which is source-only. Should the Windows package ship without `hpc`, or should the build host get the MSVC Build Tools? This only affects the one extra.
- Should uploads and skill imports reject Windows reserved names (`CON`, `NUL` and so on) on every host, or only on Windows?
