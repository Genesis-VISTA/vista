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
- **Package for Windows x64.** This adds a PowerShell launcher, a Windows build path, and preflight checks for Hypervisor Platform and path length.

## Capabilities

### New Capabilities
- `code-execution-sandbox`: streamed output, stdin delivery, exit codes, host DNS, and a store that survives upgrade, with a documented reset for rollback.
- `host-portability`: behavior that must not depend on the host OS. This covers guest paths, text encoding, host tools, line endings, and download containment.

### Modified Capabilities
- `laptop-distribution`: Windows x64 becomes a supported platform with its own preflight checks. Windows artifacts may be built on a Windows host rather than cross-built.

## Non-goals

- Windows arm64 packages. There is no test hardware.
- Code signing and ORNL endpoint-policy review. These are tracked as risks.
- Cross-building Windows artifacts from macOS or Linux.
- WSL2 as a supported route.
- The Electron window shell on Windows.
- VISTAGuard.

## Impact

- **dev_mcp_server:** the sandbox adapter, config, server, and dependency pin. `lib/dns.py` and its tests are removed.
- **backend:** `agents/agents.py`, `agents/skills.py`, `services/skills.py`, `db/seed.py`.
- **vista_mcp_server:** `display_file_mcp.py`, `submit_job_mcp.py`, `agenthpc/config.py`. The dead `lib/dns.py` is removed.
- **Scripts:** `package_launcher.sh`, `build_local_package.sh`. New: a PowerShell launcher and a Windows build path.
- **Repo:** new `.gitattributes` rules.
- **Deployment:** AWS's `/data/msb` store migrates on the first 0.7 deploy.
