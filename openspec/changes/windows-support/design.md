## Context

See [proposal.md](proposal.md) for the motivation. The facts that shape the approach:

- **The microsandbox 0.7.2 spike is done** and uncommitted on `windows-support`.
  - The dev server now runs commands through the SDK instead of `pty` and the `msb exec` CLI.
  - Live checks on macOS: 10 of 10 pass on 0.7.2. On 0.5.7, 9 of 10 pass, and `create_file` hangs because stdin was the pty slave.
  - Edge cases also pass on 0.7.2: Python output streaming, a missing program, separate byte-exact streams, and cwd and env.
  - Hermetic tests: 50 pass, down from 61; the 11 missing are the removed DNS tests.
  - The live-check scripts were in the session scratchpad, which has since been cleared. Task 1.9 restores them as marker-gated tests.
- **The 0.7 store migration is one-way.** Once 0.7.2 opens a 0.5.7 store, 0.5.7 fails with `Migration file … is missing`. Two stores are in use today:
  - Dev checkouts share `~/.microsandbox`, the default, because nothing sets `MSB_HOME`.
  - Packages use `$VISTA_HOME/microsandbox`, set by `package_launcher.sh`.
- **0.7's socket-path limit** needs `MSB_HOME` at 51 characters or less on macOS: msb rejects a derived socket path of 102 bytes or more (measured 2026-09-23 with 0.7.2; 52 characters fails, independent of the sandbox name). The launcher's `SOCKET_BUDGET` was 60.
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

Every consumer keeps its current store path: `~/.microsandbox` for dev, `$VISTA_HOME/microsandbox` for packages. 0.7 migrates each one the first time it opens it. A migration test on a copy of `~/.microsandbox` succeeded. Task 3.5 confirms that the images survive, so nothing is re-imported or rebuilt.

The cost is rollback. Once migrated, a store no longer opens under 0.5.7 (`Migration file … is missing`). The reset is to delete the store directory, after which the old version re-imports or rebuilds its image on next start. The consequences:

- **Dev worktrees are one-way from the first run.** The first time any worktree runs on 0.7, `~/.microsandbox` migrates, and every worktree still on 0.5.7 (main included, until this merges) fails to start its sandbox until it rebases or resets. To avoid that before merge, the branch carries a temporary default: when `MSB_HOME` is unset, the dev server sets it to a separate interim store, `~/.microsandbox-interim`, before the SDK or any `msb` subprocess starts. Every live run on the branch then leaves `~/.microsandbox` alone without anyone having to remember an export. The default is removed as the last commit before merge (task 8.1), and from then on the permanent in-place migration applies. The interim store can be deleted after merge.
- **Packages:** upgrading needs no action. Rolling back means deleting `$VISTA_HOME/microsandbox`, which the release notes say.

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

`scripts/package_launcher.ps1` mirrors `package_launcher.sh` step for step and is installed in the package as `vista.ps1`. `scripts/package_launcher.cmd`, installed as `vista.cmd`, lets it be double-clicked or run from `cmd`. Bash and job control have these equivalents:
- `Start-Process` for each service, and `taskkill /T` on the way out, so a service's children (the sandbox servers the backend spawns) die with it. Each service runs under `cmd /d /s /c "…"` so stdout and stderr share one log file; `/s` and the outer quotes matter, because without them a `cmd /c` line that starts with a quote and holds more than two loses its first and last quote and cmd refuses it, and no service starts
- `System.Net.Sockets.TcpClient` for port probes
- `$PSScriptRoot` for the `realpath` of the script's own folder

Preflight order:
1. platform and architecture, from `manifest.json`
2. path length (D11)
3. sandbox hypervisor
4. ports
5. store path budget: not enforced on Windows until open question 1 is answered

The hypervisor is checked by running the package's own `msb doctor`, which needs no admin rights. The host is ready when it exits 0 and reports `Host setup is ready.`; otherwise the launcher stops and shows `msb doctor`'s output. Windows' optional-feature list is **not** consulted: on the Windows build host, CIM `Win32_OptionalFeature` reported `HypervisorPlatform` as disabled (`InstallState=2`, with only `VirtualMachinePlatform` enabled) while `msb doctor` reported `✓ Hypervisor  Windows Hypervisor Platform` and the host ready. Refusing on the feature list would have stopped a working install. (Measured 2026-09-25.)

Before starting anything the launcher also points each environment's `pyvenv.cfg` `home` at the bundled interpreter (D10), and sets `PYTHONUTF8=1`. The unused `VISTA_MSB_PATH` export is dropped from both launchers.

### D10. Build Windows packages with the same script, under Git Bash

Windows packages are built by `scripts/build_local_package.sh`, run from Git Bash, not by a separate PowerShell build script. Git for Windows is already a build prerequisite, and Git Bash hands POSIX paths to native programs as Windows paths, in arguments and in the environment. One script means one set of steps, checks and comments for every platform; the Windows differences are branches inside it:
- **Layout:** environments keep `Scripts\python.exe`; `uv.exe`, `msb.exe` and `node.exe` (from the Node `win-x64` zip) carry `.exe`.
- **Copying:** Git Bash has no rsync. A `copy_tree` helper uses rsync where it exists and a tar pipe otherwise, with rsync's exclude semantics (including the anchored `/mcp-apps/`) translated to GNU tar's.
- **Relocation:** `home` in `pyvenv.cfg` is left absolute. CPython resolves a relative `home` against the working directory, not the file, so the relative form the macOS and Linux build writes does not work on Windows; the launcher re-points it at startup (D9).
- **Sandbox image:** built with podman or Docker on the Windows host itself (Podman Desktop was used), or passed in with `--sandbox-image`.
- **Archive:** a zip by default, written with Windows' own `tar.exe`; Git Bash's GNU tar cannot write zip.
- **No C++ toolchain** is needed (D13).

`launch.sh` and `build.sh` run under Git Bash for development too. The only Windows-specific change there is shutdown: bash's signals never reach native Windows programs, so `launch.sh` ends each service's process tree with `taskkill /T`.

### D11. Keep package paths short, and check them

Windows caps a full path at 260 characters unless long paths are enabled, and Explorer cannot extract a zip entry past it whatever the setting. Neither the build host nor a researcher's machine is asked to enable long paths.
- **The payload ships as one archive.** `vista-data`, `knowledge-bases` and `huggingface` are packed into `payload/payload.tar`, and the launcher extracts the parts the state directory lacks on first run. Some corpus PDF names are 150+ characters a few folders deep: inside the package that put the deepest file at 226 characters relative to the package root, and 314 when unpacked in Temp; in the state directory the same file is about 238. Done on every platform, so the launchers stay alike.
- **Package names are short:** `vista-<version>-<os>-<arch>`, e.g. `vista-0.1.0-win-x86`. The folder name precedes every path in the package, twice when Explorer's Extract All makes a folder named after the zip. The commit and dirty-tree marker stay in `VERSION` and `manifest.json`, and the manifest keeps the full OS and architecture names the launchers check.
- **Files nothing loads are not shipped** (D13): the JupyterLab extension assets and Ansible collection fixtures that were past the limit came from dependencies VISTA never uses.
- **The build records and reports it.** The manifest records `longest_relative_path`; the build prints how many characters that leaves for the unpack location, and lists the deepest files when it is under 40. The first Windows build left 62.
- **The launcher checks it.** It adds its own location's length to `longest_relative_path` and refuses before starting anything if the total passes 259 and `LongPathsEnabled` (readable without admin rights) is not 1, suggesting a shorter location such as `C:\vista`.
- **The state directory too.** The manifest also records `longest_state_path`, the deepest path `payload.tar` puts into the state directory (218 for the current corpus), and the launcher applies the same check to `VISTA_HOME`. `tar.exe` can write a file past the limit, but the backend then cannot open it and fails at startup: the first Windows smoke test hit exactly that with its state under Temp (267 characters). The default `C:\Users\<name>\.vista` leaves room for user folder names up to about 22 characters; the smoke test keeps its state under the home folder for the same reason.

### D12. No cross-platform builds, and no running without a microVM

Every artifact is built on its own platform. The only cross-build, a Linux package from a Mac inside a container (`build_in_docker.sh`), could never verify its artifact: a container has no `/dev/kvm`, so the launcher needed an opt-out (`VISTA_ALLOW_NO_KVM`) just to start, and every check through the sandbox was skipped. VISTA does not work without a microVM, so a package verified that way was not verified. `build_in_docker.sh` and `Dockerfile.build` are removed, `VISTA_ALLOW_NO_KVM` is removed from the launcher and smoke test, and the Windows launcher has no equivalent. A build host must be able to run the sandbox, and every smoke test includes retrieval.

The sandbox backend is chosen once, from `VISTA_DEV_MCP_SANDBOX_MODE` (default `microsandbox`). There is no fallback to the container backend; nothing sets that variable, and a microVM that cannot start is an error.

### D13. Exclude amscrot-py's unused providers

`amscrot-py` lists every provider's libraries as required dependencies (FABRIC, Chameleon, SENSE, Janus, AWS, GCP, Kubernetes), but VISTA uses only its IRI job client (`vista_mcp_server/lib/iri.py`). Two are excluded with uv `override-dependencies` and a marker that never matches:
- `ansible`, for the Janus provider: the full distribution, about 205 MB of collections, some of whose test fixtures were past the path limit.
- `fabrictestbed-extensions`, FABRIC's notebook library: it brought JupyterLab browser extensions (the deepest files) and, through `fabric-fim`, `recordclass`, which has no Windows wheel and needed the MSVC Build Tools.

After the change `python-chi` is the only dependency without a wheel for Windows, and it is pure Python. The MCP server environment shrank from 1.6 to 1.3 GB, and its hermetic suite passes (168 of 169; the one failure is the symlink test in 6.4). `--without-hpc` is removed: `amscrot-py` is always bundled. The overrides come out once `amscrot-py` moves its providers into extras.

## Risks / Trade-offs

- **[microsandbox on Windows is preview]** → The adapter stays small and isolated behind `SandboxProcess`. Windows-only failures are reproduced with a minimal script and reported upstream. The container backend stays available for development when selected explicitly (`VISTA_DEV_MCP_SANDBOX_MODE`); it is never a fallback (D12).
- **[Hypervisor Platform needs admin and a restart; managed ORNL laptops may forbid it]** → Preflight names the requirement before anything starts (D9). Raise it with IT before the Windows build work, since no code can remove it.
- **[Unsigned `msb.exe` and `python.exe` trip SmartScreen or Defender]** → The first-run documentation covers "More info → Run anyway". Signing is a non-goal and stays tracked here.
- **[The SDK stub and the runtime disagree (`name`)]** → A unit test pins the awaitable-property behavior, so an SDK change that makes `name` a method fails loudly.
- **[Rollback needs a manual store reset]** → The release notes and the Windows install notes give the one reset step (delete the store directory). Task 3.5 checks that the step actually restores the old version.
- **[One live run on this branch against the shared `~/.microsandbox` breaks every 0.5.7 worktree]** → The branch's interim-store default (D3) until merge, then announce the migration when the change merges.
- **[`aws/` still targets the 0.5.7 sandbox code]** → Out of scope: there is no current AWS deployment. Task 3.4 found that `aws/Dockerfile.server`'s `VISTA_DEV_MCP_OCI_IMAGE_TAR` is implemented only on unmerged branches (`f618a12` on `origin/beta-deployment-3` and `origin/s3-job-output`), which rewrite `microsandbox_sandbox.py` against the 0.5.7 CLI. If a deployment goes ahead, those branches are ported to this change's SDK path: their tar seeding becomes `Image.load` plus the existing digest comparison.
- **[Linux live sandbox is untested in this change]** → Hermetic tests only. Run the live sandbox tests (task 1.9) on a Linux host with KVM before release.
- **[The resolv.conf that microsandbox writes has mode 0700]** → This is an existing TODO. The sandbox image stays root until it is fixed upstream.
- **[The dependency overrides assume VISTA never uses amscrot-py's FABRIC or Janus providers]** → They are commented in `vista_mcp_server/pyproject.toml` with the reason and the condition for removing them. Using either provider means removing the matching override, and on Windows, for FABRIC, bringing back the MSVC Build Tools for `recordclass`. The upstream fix is for `amscrot-py` to move providers into extras; raise it with the amsc2 maintainers.
- **[A build host must be able to run the sandbox]** → Linux packages can no longer be built from a Mac, or in any container without KVM (D12). Accepted: a package built where its sandbox cannot run could not be verified.

## Migration Plan

1. Until merge, the branch's dev server defaults to `~/.microsandbox-interim` (D3). Packages built from the branch are tested only under a throwaway `VISTA_HOME`, never `~/.vista`.
2. Remove the interim default as the last commit, then merge to main in one MR, and tell developers that the first run migrates `~/.microsandbox`. Any worktree still on 0.5.7 must rebase, or delete `~/.microsandbox` and rebuild on its next start.
3. Packages: the next release migrates `$VISTA_HOME/microsandbox` on first run. The release notes give the rollback reset.

## Open Questions

- Does `msb` on Windows use named pipes, or anything else bound by a path budget? That decides whether the store-length check applies on Windows. It only changes one preflight check.
- ~~The optional `hpc` extra pulls in `recordclass`, which is source-only. Should the Windows package ship without `hpc`, or should the build host get the MSVC Build Tools?~~ Resolved by D13: `recordclass` came only through `fabrictestbed-extensions`, which is excluded, so neither is needed and `hpc` is always bundled.
- `amscrot-py` logs to `AMSCROT_LOG_LOCATION`, defaulting to `/tmp/amscrot.log`. On Windows that is `C:\tmp\amscrot.log`, and `C:\tmp` does not normally exist, so importing `amscrot.client` raises `FileNotFoundError` and IRI job submission fails. Should VISTA set `AMSCROT_LOG_LOCATION` (for example under the state directory's `logs\`) in the launchers and `launch.sh`, or when `lib/iri.py` first imports amscrot, or should amscrot fix its default upstream?
- Should uploads and skill imports reject Windows reserved names (`CON`, `NUL` and so on) on every host, or only on Windows?
