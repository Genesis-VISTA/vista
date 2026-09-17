## 1. DNS fix (separate commit, lands first)

The Globus microVM cannot reach `downloads.globus.org` without this, and the code-execution
sandbox has the same defect today. See design.md — "DNS is fixed first, as a separate commit, and
the helper is duplicated". No spec delta: this changes no stated requirement.

- [x] 1.1 Add a `host_nameservers()` helper to `dev_mcp_server` that parses `scutil --dns` on
      darwin and `/etc/resolv.conf` elsewhere, falling back to the current public resolvers with a
      warning log naming the fallback; verify with a unit test that covers a darwin fixture, a
      linux fixture, and the empty case that triggers the fallback
- [x] 1.2 Duplicate the same helper in `vista_mcp_server` for `gcp_vm.py` to use, with an identical
      unit test in that package; verify both tests pass, since cross-package import does not work
      in the packaged layout
- [x] 1.3 Replace the hardcoded `nameservers=("1.1.1.1", "8.8.8.8")` at
      `mcp_servers/dev_mcp_server/src/dev_mcp_server/lib/microsandbox_sandbox.py:128` with the
      helper's result; verify by resolving a hostname from inside a sandbox on a network whose DNS
      is internal, which fails before the change and succeeds after
- [x] 1.4 Commit separately from the rest of this change, with a message stating it fixes the
      agent sandbox's DNS independently of Globus

## 2. The Globus image

- [x] 2.1 Write a `vista-globus` Dockerfile carrying `python3`, `ca-certificates` and
      `libstdc++6` and nothing else; verify Globus Connect Personal's `_gcp_invokepython` shim
      finds an interpreter, and that `python3-dotenv` and `curl` are absent because nothing in the
      guest needs them
- [x] 2.2 Build and export it in `scripts/build_local_package.sh` alongside the sandbox image,
      reusing `sandbox_archive_arch` to check the exported archive's architecture; verify a
      deliberately wrong-architecture archive fails the build rather than loading silently
- [x] 2.3 Add the image to the build manifest; verify the recorded contents name it and its size

## 3. `gcp_vm.py`

- [x] 3.1 Create `mcp_servers/vista_mcp_server/src/vista_mcp_server/lib/gcp_vm.py` owning the whole
      endpoint flow, with `start`, `stop` and `status` callable at any time so that moving the
      trigger later is a change of caller; verify `status` reports distinctly on no image, no VM,
      a running VM, and a VM that died
- [x] 3.2 Locate the bundled `msb` binary inside `dev_mcp_server`'s environment by the same
      discovery `scripts/package_launcher.sh:223` performs, without adding `microsandbox` to
      `vista_mcp_server`'s dependencies; verify `uv sync` in `vista_mcp_server` pulls no new package
- [x] 3.3 Build the microVM arguments: `-u ubuntu`, 512 MB, `--dns-nameserver` per host resolver,
      the pre-loaded image with no dockerfile and no pull, and no mount options at all; verify by
      unit test against a fake `msb` that the argv contains no `stat-virt` and never a dockerfile,
      the trap `microsandbox_sandbox.py:32` documents
- [x] 3.4 Mount the data directory and the HPC jobs directory at matching host paths, and do not
      mount the repository root; verify by unit test that the argv contains no mount whose source
      is the repo root, and that every mount source equals its destination. The jobs directory is
      mounted read-only. A probe during this change reported that microsandbox 0.5.7 mis-parses
      `src:dst:ro`, and the option was dropped for a commit on that basis; re-probing the real
      configuration against the guest's `/proc/mounts` showed it correct on three consecutive
      runs, and `test_gcp_vm_sandbox.py` now asserts both the mount table and the refused write
- [x] 3.5 Close stdin explicitly on every non-interactive `msb exec`; verify a non-interactive
      invocation returns rather than blocking, which it does when stdin is left open
- [x] 3.6 Hold the `msb exec` running the endpoint for the session so microVM lifetime follows the
      launcher, replacing the `os.execv` the script uses today; verify the microVM is gone after
      the launcher exits

## 4. `launch_globus.py`

- [x] 4.1 Delete `relaunch_in_container`, its inline Dockerfile, the docker/podman resolution, and
      the now-unused `textwrap`, `getpass` and `shutil` imports; verify no reference to a container
      runtime remains in the file
- [x] 4.2 Reduce the script to a thin CLI wrapper over `gcp_vm.py`, so the native and microVM paths
      differ only in whether a command is prefixed with `msb exec`; verify the `-restrict-paths`
      string and the directory creation exist in exactly one place
- [x] 4.3 Confine `resolve_gcp`'s `shutil.which("globusconnectpersonal")` shortcut to the Linux
      native branch; verify by unit test that the microVM path never returns a host binary,
      which on macOS would be an unrunnable Darwin executable
- [x] 4.4 Keep installing Globus Connect Personal into the data directory, as
      `scripts/launch_globus.py:61` already does for persistence; verify a second launch performs
      no download
- [x] 4.5 Pass `-t` to `msb exec` for setup when the launcher's stdin is a tty, preserving the
      existing `sys.stdin.isatty()` test, and keep the current refusal-with-instructions when it is
      not; verify setup prompts interactively from a terminal and refuses cleanly from a pipe
- [x] 4.6 Open the login URL best-effort with `open` on darwin and `xdg-open` then `wslview` on
      linux, ignoring every failure, and always print the URL; verify setup still completes on a
      host where no opener exists

## 5. Launcher and startup

- [x] 5.1 Load the second image in `scripts/package_launcher.sh`'s first-run block; verify a second
      run does not re-import it. Both images now go through one `load_image`, whose `image inspect`
      guard gives the idempotence: driven against a fake `msb`, a first call inspects then loads and
      a second inspects only. The Globus image loads under the refresh-token gate rather than beside
      the sandbox image, and its failure is a warning rather than `die`, so an image most
      installations never use can neither delay nor prevent a start
- [x] 5.2 Start the endpoint as a fourth managed service added to `PIDS`, gated on a refresh token
      being configured and non-fatal on failure, without copying the KVM hard gate; verify that
      with no token configured the launcher starts everything else and exits 0. The gate, the
      service and the reporting were driven through five scenarios -- no token, setup refused, image
      import failed, endpoint running, endpoint died -- each reaching a distinct printed cause with
      the other services untouched. The full live launch is 9.3, which needs a built package
- [x] 5.3 Run first-run setup before the services start, matching `scripts/launch.sh:52`; verify a
      collection created during setup is visible to the MCP server in the same session. Setup runs
      at `package_launcher.sh:311` and the MCP server starts at `:362`, so the collection exists
      before the server that reads it. Setup inherits the terminal rather than writing to a log
      file, because it prints an address to log in at and waits for what the login returns
- [x] 5.4 Print a startup line naming the clusters whose file operations are unavailable when the
      endpoint did not start, in the shape of the warning block at `scripts/launch.sh:56`; verify
      the line appears when setup is skipped and is absent when the endpoint is running. The line
      names the cause, which comes from the launcher for the gate and the image and from
      `--status` for the endpoint itself. Because the endpoint serves no port, having been started
      is not the same as running: the check is retried briefly, bounded by the holder process being
      alive, so a microVM still being created is not reported as a failure

## 6. Reporting and configuration

- [x] 6.1 Change `vista_globus_collection_id` at
      `mcp_servers/vista_mcp_server/src/vista_mcp_server/config.py:197` from
      `functools.cached_property` to a plain property; verify a collection created after the server
      started is seen without a restart. `test_globus_collection_id.py` reads the absence first and
      then the collection written after it, which is the sequence the cache broke. Two existing
      tests patched the instance attribute a `cached_property` allows and a plain property does
      not; both now patch the class
- [x] 6.2 Reword the two tool errors at `submit_job_mcp.py:401` and `:722` so the remedy they name
      exists in a packaged installation, which `./scripts/launch_globus.py` does not; verify the
      message references nothing under `scripts/`. Both call sites became
      `settings.require_globus_collection(cluster)`, mirroring the `require_globus_token` beside
      it, so the message exists once rather than as two copies that can drift. Tests assert the
      absence of `scripts/` in the message from both the dispatchers and the helper

## 7. Tests

- [x] 7.1 Add `test_gcp_vm.py` with `unit` tests driving a fake `msb`, covering the argv assertions
      in tasks 3.3 and 3.4 and an actionable message for a missing image or stopped VM; verify they
      pass under the hermetic filter `not live and not hpc and not sandbox`
- [x] 7.2 Add a `sandbox`-marked test that really boots the microVM and asserts the mounts are
      visible at their host paths, the read-only mount refuses writes, and the repository root is
      absent; verify it is excluded from the hermetic filter and passes when run deliberately.
      `test_gcp_vm_sandbox.py`, seven tests, passing against a real microVM. Writing it is what
      caught the mistake recorded in 3.4: the read-only mount works, and the probe that said
      otherwise was wrong. It asserts the guest's `/proc/mounts`, not a behaviour, because a write
      refused by a read-only mount and one refused by an absent mount look identical
- [x] 7.3 Extend `scripts/smoke_test_package.sh` so that with no refresh token configured the
      launcher reports its Globus state and exits 0, using the existing `skip()` helper rather than
      `ok` for anything that could not actually run. Both tokens are unset rather than assumed
      absent, so a maintainer with them exported tests what the build tests. The endpoint itself is
      `skip`ped, because with no token none is started. The grep was checked against the launcher's
      real output

## 8. Documentation

- [x] 8.1 Remove the Docker prerequisite and the macOS `globusconnectpersonal` prerequisite from
      `README.md`, both of which become wrong; verify no remaining instruction tells a user to
      install either. Only half of this held on inspection. The `globusconnectpersonal` line is
      gone: it is wrong on every platform, since the Linux build is downloaded into the data
      directory and the macOS GUI application was never used. Docker stays for a *checkout*,
      because `dev_mcp_server` still defaults `dockerfile` to the file in its own package and the
      microsandbox backend builds that image with docker or podman on first launch. The entry now
      says what it is for, and that neither Globus nor a prebuilt package needs it
- [x] 8.2 Update the Globus paragraph in `AGENTS.md`; verify it describes the microVM and no
      container runtime. Also added a README section for the packaged artifact, which had no
      account of file transfer at all: the token to export, the one-time login, and what the
      launcher prints when there is no token

## 9. Verification I can run

- [ ] 9.1 Complete a real interactive setup under `msb exec -t` and confirm `client-id.txt` is
      written to the data directory; this is the one unproven assumption in design.md and blocks
      the rest
- [ ] 9.2 Run `./scripts/ci-local.sh` and verify it is green
- [ ] 9.3 Build the package and run `./scripts/smoke_test_package.sh` with Docker stopped; verify
      it starts and serves chat and retrieval
- [ ] 9.4 With a refresh token exported, start the package and verify the collection appears online
      in the Globus web interface

## 10. Gated, not automated

- [ ] 10.1 One end-to-end submit to Odo — submit, status with logs, fetch outputs. **Requires
      explicit approval and is run by the maintainer, not by any automation.** Do not perform this
      step, or any other that contacts an OLCF machine, without being asked
- [ ] 10.2 Frontier only after asking OLCF, and separately from Odo
