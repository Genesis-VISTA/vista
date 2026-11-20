## Context

See proposal.md for why. The pieces this touches, as they are today:

- **Sandbox mounts.** `ProjectAgent` (`backend/src/vista_backend/agents/agents.py`, where it
  builds the dev MCP server) passes the dev server a fixed list of `(host, sandbox, mode)`
  volumes in `VISTA_DEV_MCP_VOLUMES`: the project volume at `/mnt` (w) and its skills at
  `/mnt/skills` (r). The dev server mounts them when its sandbox starts and runs `mkdir` on
  every host path first (`dev_mcp_server/server.py`, `app_lifespan`). Mounts are fixed for the
  sandbox's life. Read-only mounts work under microsandbox (archived `globus-under-msb`).
- **Agent lifetime.** One `ProjectAgent` (and sandbox) per conversation, pooled by
  `(chat_session_id, project_id)`. `invalidate_agents(project_id=…)` evicts a project's agents
  after commit; a running turn or background command holds its agent (`TTLPool.hold`), so it
  keeps its sandbox until it ends.
- **Files.** The files API knows two kinds, `uploads` and `outputs`, each one folder in the
  project volume (`services/files.py` `_kind_dir`, `api/files.py` `FileKind` and its
  convertor, `services/displayed_files.py` `_ROOTS`, the UI's `FILE_KINDS`). Listing walks the
  whole folder (`rglob`). `display_file` (vista MCP) only rewrites a sandbox URI through a
  `uri_map` the backend puts in per-call metadata; the transfer tools map sandbox paths to
  host paths through the same metadata's project paths (`transfer_mcp._local_source`).
- **Project content.** `ProjectCreate` fields are project content: a change sets `modified`
  (`services/project.py`), and an import rewrites every field on an existing copy
  (`services/project_collaboration.py`). The manifest is built field by field.
- **Electron.** The main window's preload exposes `window.vistaApp` (updates only); no
  `showOpenDialog` anywhere.

## Goals / Non-Goals

**Goals:**
- One mechanism, a `linked` file kind resolved by link name, serving the Files page,
  `display_file` and the transfer tools.
- Host paths reach the sandbox only as mounts, the UI only from the project settings
  endpoints, and the agent never.

**Non-Goals:**
- Watching linked folders for changes (the Files page refreshes as it already does).
- Making absolute symlinks inside a linked folder resolve inside the sandbox (see Risks).
- Any limit on a linked folder's size or file count.

## Decisions

### Links live in their own table, not on `ProjectCreate`

A new `ProjectLinkedFolderTable` (`project_id` FK with delete cascade, `name`, `host_path`,
`created_at`; unique on `(project_id, name)` and `(project_id, host_path)`), additive under
`db/schema_check.py`. Endpoints under `/projects/{name}/linked-folders` (list with each link's
availability, add, rename, delete), each calling `invalidate_agents(project_id=…)`.

*Why:* putting it on `ProjectCreate` would make linking mark a published project modified,
an import reset it, and the "changed here" check compare it (proposal: Local only). A side
table is invisible to `project_content`, `_build_manifest` and import by construction, so
the "never leaves this computer" requirement holds without a single exclusion to forget.
*Alternative:* a JSON column next to the sharing fields on `ProjectTable`. Fewer files, but it
still rides along in `ProjectPublic` to every consumer, and uniqueness becomes hand-checked.

### Availability is checked when a sandbox's volumes are built, and when listed

A single helper resolves a project's links into `available: [(host, /mnt/linked/<name>, "r")]`
and `unavailable: [(name, reason)]` (missing, not a folder, unreadable). It resolves each stored
path again (`Path.resolve()`) and mounts the result: a path stored resolved can still come to
pass through a symlink later (a folder on the way replaced by one), and microsandbox fails the
whole sandbox start, not just that mount, on a host path through a symlink (`mount …: Not a
directory`, found in group 1). `ProjectAgent` uses
it for its volumes and for the agent's instructions; the settings and Files page use it for
their status. No background polling: a link's status is whatever it was when last asked.

*Why:* the sandbox's mounts are decided once, at start; checking anywhere else could
disagree with what the agent actually has.

### The dev server creates only folders VISTA owns

Each volume gains an `owned` flag in `VISTA_DEV_MCP_VOLUMES` (default true, so older callers
behave as today). The dev server creates owned host paths as now, and drops an unowned volume
whose host path is not an existing directory, logging it, rather than creating it or failing
the start. It resolves an unowned volume's host path before mounting it, for the same
symlink reason as above. Linked folders are sent unowned.

*Why:* the backend already filtered unavailable links, but a drive can vanish between that
check and the VM's start; the dev server is the last line that would otherwise `mkdir` an
empty folder where the researcher's data was. It is also the last line before a symlinked
path would stop the sandbox from starting at all.

### Leftover mount points are removed before each sandbox starts

A linked folder is mounted at `/mnt/linked/<name>`, inside the writable project volume at
`/mnt`, so both backends create an empty `linked/<name>` folder in the host's project volume
as its mount point and leave it there after the sandbox closes (found in group 1). Left alone,
an unlinked, renamed or unavailable link would still appear to the next sandbox as an empty
`/mnt/linked/<name>`, which the "Drive unplugged" scenario rules out. So when `ProjectAgent`
builds its volumes, it removes every *empty* folder directly under `<volume>/linked/` that is
not an available link's name, with `rmdir` only (never recursive, so anything the agent wrote
there is kept), skipping any the host refuses. The dev server creates the mount points again as it mounts.

*Why:* a sandbox path outside the project volume (`/linked/<name>`) would avoid mount points
in the volume altogether, but changes the path everywhere it is named (specs, instructions,
`uri_map`, transfer tools). Removing empty folders keeps every path as specified.

### The agent is told through its instructions, built per agent

A "Linked folders" section is appended to the project agent's instructions when the project
has links: each sandbox path, the read-only rule and the copy-to-`/mnt/data/work` rule, and
any unavailable names. It is built with the volumes, so it describes exactly the sandbox the
agent has. Host paths are never included.

### A `linked` file kind addressed by link name

Files are addressed as kind `linked` with path `<name>/<path within it>`. The backend resolves
`<name>` through the project's links on every request (never from a host path in the request),
then confines the resolved real path under the link's resolved root with `path_is_under` on
`Path.resolve()` results, so `..` and outward symlinks both fail the same check. The serving
headers are the existing ones (inline allowlist, `nosniff`, sandboxing CSP).

- **Listing** gets a new one-level endpoint for linked folders (`GET …/linked/<name>/<dir>`
  returning that directory's entries, each a file or folder, with an outward symlink omitted);
  uploads and outputs keep their recursive listing. The UI's tree fetches a linked folder's
  children when it is expanded.
- **`display_file`:** the backend adds `file:///mnt/linked/<name>/{path}` →
  `/api/files/linked/<name>/{path}` to `uri_map` for each available link, and
  `displayed_files` records kind `linked`, path `<name>/<rel>`. Shown-in then works unchanged.
  A record whose link is later renamed or removed simply finds no file and offers nothing.
- **Transfer tools:** the per-call metadata's project paths gain `linked: {name: host_path}`
  for available links; `_local_source` adds `/mnt/linked/<name>` → that root to the map it
  confines against, and resolves symlinks before the check.

*Alternative:* serve linked files from inside the sandbox (via the dev server) so host and
guest agree by construction. Rejected: it would tie the Files page to a running sandbox, which
a conversation-less Files page does not have.

### Guards are decided on resolved paths

On add: resolve the path; refuse if not a directory; refuse if it equals, is inside, or
contains `settings.data_dir` (resolved); require a `confirm_broad: true` flag when it equals
the home folder or is a filesystem root (`Path(p).anchor == p`, so `C:\` too), and return a
`409` naming the reason otherwise, which the UI turns into the confirmation dialog.

### Folder picker through the existing preload

`window.vistaApp.pickFolder()` → IPC `app:pick-folder` → `dialog.showOpenDialog(mainWindow,
{ properties: ['openDirectory'] })`, returning the path or null. The UI shows the button only
when `window.vistaApp?.pickFolder` exists, and always shows the typed-path field.

## Risks / Trade-offs

- [Windows guest cannot read a host bind mount (windows-support task 6.7)] → Checked in task
  1.1 on Windows 11 with native microsandbox: read-only mounts are readable by root and a
  non-root user and refuse every write, so Windows links like the other hosts.
- [Absolute symlinks inside a linked folder] A link `current -> /Users/me/data/salt/runs`
  that points inside the folder by absolute host path is followed by the backend but resolves
  to nothing in the guest. → Documented; relative symlinks work in both. Not worth rewriting
  mounts for.
- [The agent can read, and over the sandbox's network send, anything in a linked folder] →
  Read-only stops damage, not disclosure. VISTA's own data folder is refused outright, broad
  folders need confirmation, and the docs say plainly what linking grants.
- [Large-file read speed through the microsandbox mount] → Untested at multi-GB sizes. The
  sandbox test in group 1 times a read of a large file so a regression is visible; no target
  is promised.
- [A displayed linked file changes outside VISTA] → It is the researcher's folder, so it will.
  Shown-in already treats size or mtime changes as changed; nothing new.
- [Removing a mount point on the host while another conversation's sandbox still has it
  mounted (a held agent, e.g. a running background command)] → Checked live in task 1.3: under
  microsandbox the `rmdir` succeeds and the guest's mount stays readable; Docker Desktop
  refuses the `rmdir` while the container runs, which 2.3 skips. Either way the running mount
  is untouched, so no held-agent check is needed.
- [Renaming a link breaks the agent's earlier references to `/mnt/linked/<old>`] → Accepted;
  the next turn's instructions name the new path.

## Migration Plan

Additive: a new table, created by `init_db`. No existing row changes; a project has no links
until the researcher adds one. Rollback is dropping the feature; the table is ignored by an
older build.
