## Context

See proposal.md for why. What exists:

- `lib/globus.py`'s `GlobusClient` already has everything these tools need. Over the HTTPS
  interface it has `stat`, `download_file` (streamed to a temporary file, then moved into
  place) and `upload_file` (a `PUT` that does not create parent folders). On the Transfer API it
  has `operation_ls`, `operation_stat` and `operation_mkdir_p(parents_below=...)`. It also
  raises separate errors for an expired session and a missing file (`GlobusSessionExpired`,
  `GlobusFileNotFound`). There is no Transfer-task submission, and this change adds none.
- `submit_job_mcp.py` holds the only callers today. `_get_olcf_job_outputs` checks each
  requested name with `_job_output_paths`, skips files that are already local and not empty,
  and downloads the rest one `GET` at a time. A missing file currently fails the whole call.
  `RemoteLayout` names the folders on the cluster. On OLCF, `.jobs` needs the researcher's
  POSIX user, which comes from `GlobusClient.home_owner`.
- The backend already sends `output_dir` and `uploads_dir` with every MCP call
  (`ProjectPaths` in `lib/user_config.py`), mapped in the sandbox to `/mnt/data/output` and
  `/mnt/data/uploads`. It sends the researcher's credentials only to tools named in
  `HPC_TOOLS` (`_build_vista_metadata` in `agents.py`), so the three new tool names have to
  join that list. That one-line backend change is the only one: without it, every transfer
  call reads as "Globus not connected" whatever the researcher has connected.
- No VISTA tool asks the researcher for approval. The `requires_approval` hook in the backend
  (`agents.py`) is wired up but no tool registers it, and `submit_hpc_job` does not ask either.

## Goals / Non-Goals

**Goals:**
- One download code path for job outputs and general downloads, so caching, streaming and
  path confinement can't drift apart.
- Every argument check runs before any Globus call, so a refused call costs no round trip and
  leaves nothing behind.

**Non-Goals:**
- Changing `get_hpc_job_outputs`' arguments, results or failure behaviour.
- A recursive (folder) upload or download. An agent can list a folder and pass its files.

## Decisions

**Tools, not a skill, with the guidance in their descriptions.** A skill only reaches the agent
when it is attached to a project (`stage_skills` copies `project.skills`), and the sandbox it
would drive has no Globus credentials. Two other ways for the server to carry guidance were
considered:
- *MCP prompts* are templates a user picks. The backend never lists them, and the model never
  sees them.
- *MCP server `instructions`* (the optional text in the initialize handshake) would reach the
  model if `agents.py` passed `include_instructions=True`. But that opens a channel every chat in
  every project pays tokens for, and only the top-level server's text is sent, not that of
  mounted servers like this one.

The guidance here is per-tool (leave bulk data on the cluster; uploads go to `transfers/`), and
tool descriptions already reach the model on every turn.

**Uploads only under `<remote_dir>.<user>.jobs/transfers/`.** With no approval step, the folder
is the only thing between an agent and a researcher's real files. It sits inside the folder the
researcher's identity already owns, so it needs no new permission rule. `mkdir -p` with
`parents_below` set to `RemoteLayout.parent`, as the job-source upload does, creates `.jobs`
if no job has been submitted yet, then `transfers/` and anything below it. Jobs
already read sources from that same tree, so a job can read uploads.
- A sibling `<remote_dir>.<user>.data` would have a clearer name, but would add a third OLCF
  folder and its own existence and permission handling.
- `.out` is shared with the project group, so an upload there could be overwritten.

**Reads have no fence and no cap.** The facility already enforces what the researcher's identity
can read, and browsing project data is most of the value. The size policy follows
`download_file`'s documented rule: bulk data stays on the cluster, and any limit would be a
guess. The listing reports sizes, so the agent can see what it is about to pull.

**Downloads mirror the remote path.** `/<p>` on `<cluster>` is saved at
`<output_dir>/transfers/<cluster>/<p>`. That can't collide (two clusters, or two folders, with
the same file name), shows where the file came from, and lets the existing "already local and
not empty" check work as a cache with no record kept. Job outputs keep their `<job id>/`
destination.

**Shared helper, per-file outcome.** The per-file part of `_get_olcf_job_outputs` becomes a
helper. It takes a function that builds the client, a collection, a remote path and a local
path, skips the file if it is already local, and otherwise downloads it. The client is built
only when a file has to be fetched, so a call served from local copies needs no Globus
credential, as before. `GlobusFileNotFound` and `GlobusSessionExpired` propagate from it. The transfer tool catches `GlobusFileNotFound` per file and
reports it, and lets an expired session fail the whole call. `get_hpc_job_outputs` keeps
letting both propagate, so its behaviour is unchanged. `_job_output_paths` is generalised into a
path check for both tools: it confines a relative name under a root, both as a POSIX and as a
Windows path, and refuses backslashes. Its existing tests in `test_output_paths.py` keep passing.

**Sandbox paths, not host paths, in arguments and results.** Upload sources are given as
`/mnt/data/output/...` or `/mnt/data/uploads/...` and mapped through `ProjectPaths`. A path
that resolves outside its root after following `..` or symlinks is refused. The agent only ever
sees sandbox paths, as with `get_hpc_job_outputs` and `display_file`.

**A separate `transfer_mcp.py` server, mounted under the name `transfer`.** It keeps
`submit_job_mcp.py` from growing (it is already 2,000 lines), and `VISTA_MCP_DISABLE_SERVERS`
can turn it off on its own. It imports the layout and collection-id helpers from
`submit_job_mcp.py` rather than duplicating them.

**A recursive listing skips hidden folders.** In the first live try, one job's output folder
listed 1,818 entries, almost all under the `.cache/pip`, `.cache/matplotlib` and `.local`
trees the job's Python left behind, to find one PNG. Each folder walked is a sequential
Transfer call. The walk already prunes by exact name (`exclude_segments`), which can't express
"every name starting with `.`", so `operation_ls` gains `skip_hidden`. A pruned folder is still
listed and marked as not walked, so the agent can see it and list it by its own path.
Filtering the results afterwards would save tokens but not the round trips.

**Remote paths must be absolute.** The HTTPS interface takes absolute paths, and accepting
`~/` would mean resolving the home folder over Transfer, which isn't worth an extra round trip
for this.

## Risks / Trade-offs

- [An agent pulls a multi-GB file onto a laptop] → The listing shows sizes, and the descriptions
  say to leave bulk data on the cluster. A cap can be added later without changing the tool's
  shape.
- [An upload replaces an earlier upload the researcher still wanted] → Only files under
  `transfers/` can be hit, and the result names every remote path written.
- [Files `PUT` through Globus might be created with a mode the automation user can't read] →
  Job sources already rely on the same path being readable, and live verification (tasks group
  5) checks it for an uploaded file.
- [Moving the download loop changes `get_hpc_job_outputs`] → Its tests run unchanged, and the
  helper keeps raising.
