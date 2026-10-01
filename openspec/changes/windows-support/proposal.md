## Why

Some researchers who would use VISTA have only Windows laptops. [`prebuilt-laptop-package`](../prebuilt-laptop-package/proposal.md) ruled out native Windows because microsandbox had no Windows build. That is no longer true: 0.7.2 ships `win_amd64` and `win_arm64` wheels. An audit found that the remaining blockers are VISTA's own code and packaging, not platform limits.

## What Changes

- **Upgrade the sandbox runtime** from microsandbox 0.5.7 to 0.7.2. Commands run through the SDK's streaming exec instead of `pty` and the `msb exec` CLI. `pty` is what stops the dev server importing on Windows. The upgrade also fixes `create_file`, which hangs on 0.5.7 because its stdin never reaches the guest. *Already implemented as a spike; see tasks group 1.*
- **Use the host's DNS inside the sandbox**, including VPN split-DNS. **BREAKING (internal):** VISTA's own nameserver discovery (`dev_mcp_server/lib/dns.py`) is removed.
- **Keep sandbox-side paths POSIX on every host.** This covers volumes, skill locations, `display_file` URIs, and job-output paths.
- **Contain job-output downloads to the job's output directory.** On Windows, `/etc/x` currently escapes it. This is a security fix.
- **Read and write text as UTF-8 explicitly.** On Windows, seeding currently crashes on the cp1252 default.
- **Remove the POSIX-only host tools from the agent path.** Today every agent start runs a `chmod` subprocess.
- **Pin LF line endings with `.gitattributes`** without rewriting anything in existing macOS and Linux checkouts.
- **Migrate the sandbox store in place.** The 0.7 migration is one-way, so rolling back, or running an older checkout, needs a documented store reset.
- **Package for Windows x64.** Windows packages are built by the existing `build_local_package.sh`, run under Git Bash; the package's launcher is PowerShell, so a researcher needs no bash. The launcher checks, before starting anything, that the sandbox's hypervisor is available (asked of `msb doctor`) and that the unpack location is not too deep for Windows' path limit. `launch.sh` and `build.sh` also run under Git Bash for development.
- **Keep package paths short, on every platform.** The corpus, vector store and embedding weights ship as one archive, `payload/payload.tar`, extracted into the state directory on first run; some corpus PDF names would otherwise put paths far past 260 characters inside the package. Package names become `vista-<version>-<os>-<arch>` (e.g. `vista-0.1.0-win-x86`), with the commit kept in `VERSION` and the manifest.
- **No cross-platform builds.** **BREAKING (internal):** `scripts/build_in_docker.sh` and `scripts/Dockerfile.build` are removed. Each platform is built, and fully verified, on its own OS.
- **No way to run without a microVM.** The `VISTA_ALLOW_NO_KVM` opt-out is removed and has no Windows counterpart, so every build's verification includes retrieval through a real sandbox.
- **Drop dependencies VISTA never loads.** `amscrot-py` requires every one of its providers' libraries; VISTA uses only its IRI job client. `ansible` and `fabrictestbed-extensions` are excluded with uv overrides, which also removes `recordclass`, so no C++ build tools are needed on Windows. `--without-hpc` is removed: `amscrot-py` is always bundled.

## Capabilities

### New Capabilities
- `code-execution-sandbox`: streamed output, stdin delivery, exit codes, host DNS, and a store that survives upgrade, with a documented reset for rollback.
- `host-portability`: behavior that must not depend on the host OS. This covers guest paths, text encoding, host tools, line endings, and download containment.

### Modified Capabilities
- `laptop-distribution`: Windows x64 becomes a supported platform with its own preflight checks. Building for another platform is removed: every artifact is built and verified on its own platform, on a host that can run the sandbox.

## Non-goals

- Windows arm64 packages. There is no test hardware.
- Code signing and ORNL endpoint-policy review. These are tracked as risks.
- Cross-platform builds of any kind.
- WSL2 as a supported route.
- The Electron window shell on Windows. (Since added, following `electron-desktop-shell` P1.)
- The AWS deployment. There is none today; a future one would port the unmerged `beta-deployment-3` sandbox changes onto this change's SDK path.
- VISTAGuard.

## Impact

- **dev_mcp_server:** the sandbox adapter, config, server, and dependency pin. `lib/dns.py` and its tests are removed.
- **backend:** `agents/agents.py`, `agents/skills.py`, `services/skills.py`, `db/seed.py`.
- **vista_mcp_server:** `display_file_mcp.py`, `submit_job_mcp.py`, `agenthpc/config.py`. The dead `lib/dns.py` is removed. `pyproject.toml` gains uv `override-dependencies`, and `uv.lock` is relocked.
- **Scripts:** `package_launcher.sh`, `build_local_package.sh`, `smoke_test_package.sh`, `launch.sh`, `build.sh`, `build_in_docker.sh` (removed), `Dockerfile.build` (removed). New: `package_launcher.ps1` and `package_launcher.cmd`.
- **Docs:** `README.md` (package names, first run, the cross-platform build sections removed), `AGENTS.md` (Git Bash on Windows).
- **Repo:** new `.gitattributes` rules.
