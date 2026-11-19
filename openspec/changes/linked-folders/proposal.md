## Why

Uploads are capped at 20 MB (#96), and a researcher's real data (simulation trees,
trajectories, multi-GB datasets) is far larger and already sits in a folder on their
computer. Copying it into VISTA is slow, doubles the disk it takes, and leaves two copies to
drift apart. The agent should be able to read that folder where it is.

## What Changes

- **Linked folders.** A project can link any number of folders on this computer. Each is
  mounted **read-only** into every conversation's sandbox at `/mnt/linked/<name>`, the name
  defaulting to the folder's own and editable by the researcher.
- **Linking.** In the project's settings: a native folder picker in the VISTA window, and a
  typed path for the browser-only dev UI. VISTA's own data folder (or one inside or holding
  it) is refused; a home folder or a filesystem root asks for confirmation first.
- **The agent is told.** Its instructions list each linked folder and say that they are
  read-only, so a file to be changed is copied into `/mnt/data/work` first. A folder that is
  missing when a sandbox starts is left out, never created, and named to the agent and in the
  UI as unavailable.
- **Changes take effect on the next turn.** Linking, renaming or unlinking rebuilds the
  project's agents; a running turn or background command keeps the mounts it started with.
- **Files page and `display_file`.** A Linked section on the Files page browses each linked
  folder one directory at a time, with the usual previews and download and no upload, delete
  or rename. `display_file` can show a file from a linked folder, and that display is
  recorded like any other.
- **Symlinks stay inside.** A path whose real location is outside its linked folder is not
  listed, served or uploaded.
- **HPC upload.** The transfer tools accept local files under `/mnt/linked/<name>`.
- **Local only.** Links belong to this computer: publishing a project never sends them,
  importing one brings none, and neither touches the links already here. They are not
  project content, so changing them does not mark a published project modified.

Not in this change: AmSC hosted data services (the issue's second item; a separate change),
streaming uploads or a higher upload limit, links per conversation, writable links, and any
hint in a published project of the folders it expects.

## Capabilities

### New Capabilities
- `linked-folders`: linking a local folder to a project, its read-only mount in the sandbox,
  what the agent is told, unavailable folders, the guards on what can be linked, and that
  links never leave this computer.

### Modified Capabilities
- `code-execution-sandbox`: Sandbox volumes gains a read-only mount scenario, and a mount's
  host folder is created only when VISTA owns it.
- `project-files`: the Files page gains a Linked section; previews, serving rules, displayed-file
  records and shown-in apply to linked files.
- `hpc-file-transfer`: uploading local files accepts paths under `/mnt/linked/<name>`.

## Impact

- **Backend:** a new local table for links (additive), project API endpoints to list, add,
  rename and remove them, the sandbox volume list in `agents/agents.py`, the agent's
  instructions, the files API and displayed-file records (a `linked` kind), and the
  per-call metadata the vista MCP server reads (`uri_map`, project paths).
- **dev_mcp_server:** creates a mount's host folder only for VISTA-owned volumes.
- **vista_mcp_server:** `display_file` and the transfer tools' local-path map.
- **UI:** project settings modal (link list, picker, rename, unavailable state), the Files
  page's Linked section (lazy tree), `FILE_KINDS`.
- **Electron:** `window.vistaApp.pickFolder()` through the main window's preload and an IPC
  handler calling `dialog.showOpenDialog`.
- **Windows:** depends on a read-only host bind mount being readable in the guest, an open
  question from windows-support (its task 6.7); checked first here.
- **Docs:** a researcher-facing page on linked folders, listed in `docs/site.toml`.
