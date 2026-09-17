## Context

See proposal.md — Why, for motivation.

Current state, verified by probe on this machine rather than assumed:

- `scripts/launch_globus.py` runs on the host, and on any non-Linux platform calls
  `relaunch_in_container`, which `os.execvp`s into `docker run … python3 scripts/launch_globus.py`.
  The script therefore executes a second time inside the container, where `sys.platform` is now
  `linux`, and falls through to install Globus Connect Personal, run setup, and `os.execv` into the
  endpoint. The container mounts the repository root, because that is where the script must be
  found.
- microsandbox already runs on both supported platforms. `dev_mcp_server` spawns a microVM for
  agent-generated code. No part of VISTA itself runs inside a microVM, and this change does not
  put any there.
- The packaged artifact installs only `scripts/package_launcher.sh`, as `vista`. The rest of
  `scripts/` is absent, so a packaged installation cannot re-exec `launch_globus.py`.
- Each MCP server gets its own virtual environment in the package, so `vista_mcp_server` cannot
  import `dev_mcp_server`.

Constraints established empirically, each of which invalidated an earlier assumption:

- Globus Connect Personal refuses to run as root, so the microVM must run as a non-root guest user.
- microsandbox's bind identity map rewrites the host owner to the configured guest user, so a host
  directory with ordinary permissions is writable from the guest with no mount options. The
  `stat-virt=off` mount option disables this and must not be used.
- microsandbox forwards DNS to the nameservers in the host's `/etc/resolv.conf`, which on macOS
  contains none, while `microsandbox_sandbox.py` overrides with hardcoded public resolvers that a
  network with internal DNS does not answer. Every name lookup in a guest fails on such a network
  today.
- `msb exec` blocks on an open stdin, so a non-interactive invocation must close it explicitly.
- Globus Connect Personal requires a system `python3` on `PATH`; its `_gcp_invokepython` shim
  searches for one and exits without it.

## Goals / Non-Goals

**Goals:**

- Restore Odo and Frontier file operations to the packaged artifact with no host container runtime.
- Keep one implementation of the transfer-endpoint flow, so the Linux native path and the microVM
  path cannot drift.
- Narrow what the transfer endpoint can see on the host, relative to today.
- Leave the module able to start the endpoint on demand, so moving the trigger to the interface
  later is a change of caller rather than a change of architecture.

**Non-Goals:**

- Supervising or restarting the microVM.
- Any change to how Globus credentials are obtained or stored.
- Making the code-execution sandbox and the transfer endpoint share an image, a virtual machine,
  or a code path.

## Decisions

### The guest runs only `globusconnectpersonal`

The host-side module performs every step that does not require a Linux binary: resolving and
installing Globus Connect Personal into the data directory, creating the microVM, and deciding the
arguments. Only the endpoint itself executes inside.

The alternative is today's arrangement, where the launch script is its own container entrypoint and
runs a second time inside. That cannot survive packaging twice over: a packaged installation has no
`scripts/` directory to re-exec, and the replacement module lives inside a package whose
dependencies are not present in the guest. Keeping it would mean shipping a script and a virtual
environment into the microVM to avoid moving thirty lines of logic.

Consequences: the image loses `python3-dotenv` and `curl` and keeps `python3` only because Globus
Connect Personal needs one. `os.execv`'s "become the endpoint" behaviour is replaced by holding an
`msb exec` subprocess for the session, which is what ties microVM lifetime to the launcher.

### One module owns both platforms

`gcp_vm.py` owns the whole flow, and the native and microVM paths differ only in whether a command
is prefixed with `msb exec`. `scripts/launch_globus.py` becomes a thin CLI wrapper for development
checkouts.

The alternative — the script owning Linux and the module owning everything else — writes the
directory setup and the `-restrict-paths` string twice, where a change to one silently diverges
from the other. `-restrict-paths` is a confinement boundary, so divergence there is a security
defect rather than an inconsistency.

One consequence needs care: `resolve_gcp` begins with `shutil.which("globusconnectpersonal")`. That
is correct on the Linux native path and wrong under the microVM, where it would find a macOS binary
that cannot execute in a Linux guest. The shortcut is confined to the native branch.

### The module lives in `vista_mcp_server`, not in `scripts/` or the launcher

It ships, unlike `scripts/`, and it is already the package that reads the collection identifier.
Implementing the orchestration in `package_launcher.sh` instead would put the mount and argument
logic in shell for the packaged path and in Python for the development path — the same duplication
the previous decision rejects. That the module is not an MCP tool is a cosmetic awkwardness
accepted deliberately.

The bundled `msb` binary inside `dev_mcp_server`'s environment is invoked directly, by the same
discovery the launcher already performs, rather than adding `microsandbox` to `vista_mcp_server`'s
dependencies.

### Mount only the data directories

The microVM mounts the data directory read-write and the HPC jobs directory read-only. The
repository root is not mounted, which removes the transfer endpoint's current ability to read
`.env` and both deployment refresh tokens. `-restrict-paths` is retained as a second layer inside
the guest.

This is a security improvement that falls out of the previous decisions rather than one bought
separately, and it is recorded here so it is not reversed by a later refactor that treats the repo
mount as incidental.

### Default mount behaviour, and never `stat-virt=off`

No mount options are passed. microsandbox's bind identity map already maps the host owner to the
guest user, so a host directory created with ordinary permissions is writable from a non-root
guest, and files the guest creates land on the host owned by the invoking user. Widening host
permissions, which an earlier reading of the behaviour appeared to require, is not necessary and is
not done.

`stat-virt=off` passes real host ownership through and denies every write. It must not be set, and
the reason is recorded because the option name suggests a harmless simplification.

### Setup stays automatic and interactive

First-run setup keeps today's shape: it runs at launch, and `msb exec -t` supplies the guest a
pseudo-terminal when the launcher's stdin is a tty, the direct analogue of the existing
`docker run -t`. The login URL is always printed; a browser is opened best-effort and every failure
to do so is ignored, because Globus Connect Personal's own instruction is to copy the URL into any
browser. A host with no browser, including a WSL installation without one, therefore still
completes setup.

Deferring setup to an explicit subcommand was considered. It avoids a browser opening during
startup and avoids startup blocking on a prompt, but it is the same idea as moving login into the
interface, and deciding it twice invites two different answers. It is left to that change.

### No supervision

A microVM that dies is reported, not restarted. Status is exposed by the module; the launcher
prints a line at startup in the shape of the existing warning block, and the two job tools that
depend on transfer report the cause. Restarting a process that may be failing for a durable reason
produces a machine that spins rather than one that recovers.

### DNS is fixed first, as a separate commit, and the helper is duplicated

Resolving the host's real nameservers is a defect fix for the code-execution sandbox that the
Globus microVM happens to depend on, so it lands first and separately. The fifteen-line helper is
duplicated in both packages rather than shared: cross-package import does not work in the packaged
layout, and routing the value through an environment variable set by the launcher fails whenever
either server runs outside it, which `dev_mcp_server` does whenever the backend spawns it or its
tests run. Both packages would need the fallback regardless, so a shared channel adds coupling on
top of the duplication instead of replacing it.

The fallback remains the public resolvers, with a log line. The absence of that line is what let
this defect stay invisible.

## Risks / Trade-offs

- **Interactive setup under `msb exec -t` is unproven end to end.** The flag exists and is the
  direct analogue of the working `docker run -t`, and the interactive flow is known to work in a
  container, but the two have not been demonstrated together. → It is the first verification step,
  before any other work is validated, and it requires only a terminal and a Globus login.

- **A long-lived microVM, rather than the ephemeral ones microsandbox is used for today.** →
  512 MB was sufficient in every probe. Failure is reported rather than hidden, and costs the two
  OLCF clusters while chat, retrieval, the sandbox and Perlmutter continue.

- **The package grows by a second image.** → The image carries no Globus Connect Personal, which is
  downloaded into the data directory on first run, so it is far smaller than the probe image
  measured during feasibility work.

- **First run needs network, where a fully offline first run would otherwise be possible.** →
  Setup requires a browser login to Globus regardless, so no offline first run existed to lose.

- **Duplicated DNS helper can drift.** → A test in each package asserting the same behaviour.

- **A volume-option defect that was reported during this change and does not exist.** A probe
  concluded that microsandbox 0.5.7 mis-parses `src:dst:ro` into a read-write mount at `dsto`, and
  the read-only mount was removed for a commit because of it. Re-probed in the configuration the
  code actually uses — the data directory read-write, the jobs directory `:ro` — the guest's own
  `/proc/mounts` shows `ro,relatime` at the correct path on three consecutive runs, `msb inspect`
  agrees, and a write is refused with `Read-only file system`. The read-only mount stands. → The
  lesson is the one the first probe failed: assert against the guest's mount table, never against
  a behaviour two different causes produce. A write refused because a mount is read-only and a
  write refused because the mount is absent are indistinguishable, and `test_gcp_vm_sandbox.py`
  now checks the table.

- **A failure mode that looks like the change's fault but is not.** microsandbox derives a Unix
  socket path from its store location and fails above 104 bytes. → The packaged launcher already
  budgets for this and the Globus microVM shares that store; recorded so a stray failure during
  development is recognised rather than investigated from scratch.

## Migration Plan

No data migration. The commits land in order: the DNS fix, then this change. A deployment already
set up keeps working — the collection identifier and Globus Connect Personal's configuration live
in the data directory and are untouched, so an existing installation does not repeat setup.

Rollback is reverting the commits. The Linux native path is unchanged throughout, so a Linux
deployment is unaffected by either direction.

## Open Questions

None. The remaining unknown, whether interactive setup completes under `msb exec -t`, is a
verification step rather than a deferred decision: the specs, the approach and the task breakdown
are the same whichever way it resolves, and if it fails the fix is in how the terminal is passed,
not in what is being built.
