## Why

The desktop package dropped Globus, and with it every file operation for Odo and Frontier,
because Globus Connect Personal ships a scriptable CLI on Linux only. On macOS the workaround
was to re-exec the Linux binary inside a Docker container, and the packaged artifact refuses to
require a host container runtime.

That dependency is not Globus's. VISTA already bundles microsandbox, which needs no daemon and is
already present on every supported platform for the code-execution sandbox. Running Globus Connect
Personal in a microVM instead of a container restores Odo and Frontier to the packaged app with no
new host prerequisite.

## What Changes

- Globus Connect Personal runs in a microsandbox microVM built from its own `vista-globus` image,
  never the agent's `vista-sandbox` image, which executes arbitrary agent-generated code.
- `relaunch_in_container` and its inline Dockerfile are removed. A new host-side module starts,
  stops and reports on the microVM; `scripts/launch_globus.py` becomes a thin CLI wrapper over it.
- The guest runs only `globusconnectpersonal`. Nothing of VISTA's runs inside the microVM, where
  today the launch script re-executes itself.
- The microVM mounts the data directory read-write and the HPC jobs directory read-only. It no
  longer mounts the repository root, so it can no longer read `.env` or the deployment's Globus
  refresh tokens. **BREAKING** for any workflow relying on repo paths being visible to the
  transfer endpoint; no such workflow exists in this repo.
- First-run setup keeps its interactive browser login. The login URL is always printed, and a
  browser is opened best-effort so a host without one still works by copy-paste.
- Credentials are unchanged: the existing deployment-wide `VISTA_MCP_ODO_GLOBUS_REFRESH_TOKEN` and
  `VISTA_MCP_FRONTIER_GLOBUS_REFRESH_TOKEN` environment variables. Per-user tokens and in-app login
  are explicitly out of scope.
- Landing first as a separate commit: microsandbox DNS is configured from the host's real
  nameservers instead of hardcoded public resolvers, which resolve nothing on a network whose DNS
  is internal. This is a pre-existing defect affecting the code-execution sandbox today; the
  Globus microVM cannot reach `downloads.globus.org` without it.

## Capabilities

### New Capabilities

None. This change restores an existing capability on a platform that lost it.

### Modified Capabilities

- `laptop-distribution`: "Self-contained artifact" currently requires that no host container
  runtime be needed and names only the code-execution sandbox. It is extended so the guarantee
  covers every capability the artifact offers, file transfer included, and so the artifact is
  required to carry whatever a local transfer endpoint needs.
- `zero-config-startup`: "Optional file-transfer setup never blocks startup" lists "an absent host
  prerequisite" among the reasons setup may fail. On a packaged host that reason no longer exists,
  and the requirement gains the interactive-login behaviour that replaces it — the login URL
  presented rather than assumed, and a host with no browser still able to complete setup.

Both capabilities currently exist only as delta specs inside the unarchived
`prebuilt-laptop-package` change, not yet under `openspec/specs/`. That change is marked complete;
its specs need to be synced or archived before these deltas have a base to modify.

## Impact

- `scripts/launch_globus.py` — `relaunch_in_container`, the inline Dockerfile, and the
  docker/podman resolution are removed; `resolve_gcp`'s `shutil.which` shortcut is confined to the
  Linux native path, where it is correct.
- `mcp_servers/vista_mcp_server/src/vista_mcp_server/lib/gcp_vm.py` — new; owns the whole flow,
  with the native and microVM paths differing only in whether a command is prefixed with
  `msb exec`.
- `mcp_servers/vista_mcp_server/src/vista_mcp_server/config.py` — `vista_globus_collection_id`
  stops being a `functools.cached_property`, which never re-reads after a collection is created.
- `mcp_servers/vista_mcp_server/src/vista_mcp_server/submit_job_mcp.py` — two tool errors direct
  the user to run a script that packaged installations do not contain.
- `mcp_servers/dev_mcp_server/src/dev_mcp_server/lib/microsandbox_sandbox.py` — hardcoded
  nameservers replaced; fixes the agent sandbox's DNS on networks with internal resolvers.
- `scripts/build_local_package.sh` and `scripts/package_launcher.sh` — build, export and load a
  second image; start Globus as a fourth managed service under the existing gate.
- No new dependency. The bundled `msb` binary in `dev_mcp_server`'s environment is invoked
  directly rather than added to `vista_mcp_server`'s requirements.
- Documentation in `README.md` and `AGENTS.md` names Docker and a macOS `globusconnectpersonal`
  install as prerequisites; both become wrong.
