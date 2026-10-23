## Context

See proposal.md for why. The transfer tools live in `transfer_mcp.py` on the vista MCP server.
Each tool is a thin `@mcp.tool` wrapper over a `_list_files` / `_download_files` /
`_upload_files` helper that takes plain values (`UserConfig`, paths), and the tests call those
helpers with a faked Globus client.

Two ways to ask the researcher already exist:

- **MCP elicitation.** A tool calls `ctx.elicit(...)`. The backend's `elicitation_callback`
  turns it into an `mcp_form_elicitation` event, and the UI shows `ElicitationModal`. The Lux
  SSH login (`lib/ssh.py`) uses this. A run with elicitation off (the one-shot `/agent` call
  with no `chat_session_id`) answers every elicitation with `cancel`.
- **PALISADE approval.** A PydanticAI tool that defers for approval is resolved by
  `PalisadeApprovalCapability` and shown in `ToolApprovalModal` as the raw arguments. No tool
  uses it yet.

Downloads are cached by `_fetch_once` (in `submit_job_mcp.py`, shared with
`get_hpc_job_outputs`), which treats any non-empty local file as current. `GlobusClient`
reads file sizes with an HTTPS `HEAD`. Whether the HTTPS interface sends `Last-Modified` is
undocumented and has not been probed. Transfer `ls` and `stat` document `last_modified` on every
entry (`"YYYY-MM-DD HH:MM:SS+00:00"`, UTC, whole seconds), and `operation_ls` already returns
the raw entries.

## Goals / Non-Goals

**Goals:**
- Only the tool knows what a call will really do (resolved destination, sizes, overwrites), so
  the tool does the asking.
- A refused argument still costs no Globus call, and a declined call costs no write.

**Non-Goals:**
- Any change to `get_hpc_job_outputs`, `_fetch_once`, or the job tools.
- Using or changing the PALISADE approval path.
- A purpose-built transfer prompt in the UI. The tool's message is plain text in the existing
  modal.
- Moving the upload fence.

## Decisions

**The tools ask through MCP elicitation, not the PALISADE gate.** The gate sees only the
arguments: a list of paths, with no sizes, no `.<user>.jobs` resolution and no overwrites. It
would also be the first real use of an untested path. Elicitation is already end to end, and
can be tested in the MCP server with no backend.

**Helpers take an `approve` callable.** It is
`approve: Callable[[str], Awaitable[bool]]` (message in, approved out). The tool wrapper builds
it from `ctx.elicit(message, response_type=None)`: `AcceptedElicitation` → `True`, declined or
cancelled → `False`. The helpers stay free of `Context`, so tests pass a fake approver that
records the message and answers. The approval step is a small shared function that raises a
`TransferNotApproved` (`ToolError`) with the agreed text when the answer is no.

**One failure for declined, dismissed and "cannot ask".** The MCP server sees all three as a
no (a run with elicitation off answers `cancel`, as a dismissed modal does). A separate signal
would need a backend change and a new metadata flag, and the agent's next step is the same:
stop and ask the researcher. So the message covers all three: not approved, don't retry, ask
the researcher, and transfers need approval in a chat.

**Download metadata comes from Transfer `ls`, one call per parent folder.** The requested files
are grouped by parent folder, each folder is listed once (not recursive), and the entries are
matched by name. Size and `last_modified` come from the same entry, and a name absent from
its folder, or a folder that is missing (`GlobusFileNotFound`), counts as missing. This replaces
the planned per-file HTTPS `HEAD`: `ls` documents the modification time, `HEAD` does not, and
`ls` costs one call for many files in one folder.
- *Alternative:* Transfer `stat` per file. Documented and simpler, but N calls.
- *Alternative:* HTTPS `HEAD` with `Last-Modified`. It needs a live probe first and might not
  exist.

**The local copy's mtime holds the remote modification time.** After `download_file` moves the
complete file into place, `os.utime` sets its mtime to the remote `last_modified`. A copy is
current when it is non-empty, its size equals the remote size, and `int(st_mtime)` equals the
remote time in epoch seconds. With no `last_modified`, size alone decides. This needs no sidecar
and nothing outside the sandbox. A local edit changes the mtime, so the file is re-fetched with
approval, which the spec accepts. `download_file` already writes to a partial file and replaces,
so re-fetching a stale copy is atomic.

**A transfers-only fetch path.** `_download_files` stops calling `_fetch_once`. It classifies
each target as missing, current, new or changed, then asks, then calls `download_file` directly
for new and changed files, then sets their mtime. The job-output cache does not change.

**Order inside `_download_files`:**
1. Argument checks.
2. Credential and `ls` per folder.
3. Classify.
4. If nothing moves, report.
5. Ask.
6. Fetch and set the mtime.
7. Report.

If step 2 raises `GlobusSessionExpired`, or the not-connected `ToolError` from
`require_globus_token`, and every target has a non-empty local copy, the copies are reported
with a warning naming the cluster and the error's message. Otherwise the error propagates as
today.

**Order inside `_upload_files`:**
1. Argument checks.
2. `_layout` and `home_owner`, to resolve `target`.
3. Transfer `ls` of `target`. `GlobusFileNotFound` means nothing is replaced.
4. Ask.
5. `mkdir -p`.
6. Upload.

The `ls` uses the same `skip_hidden` / non-recursive call as listing.

**Prompt text.** Plain text, one file per line, sizes from `_human_size`, for example:

```
Upload 2 files (1.3 MB) to Frontier, into /lustre/orion/abc/r.jdoe.jobs/transfers/inputs/:
  salt.xyz       12.0 KB   replaces the file there
  mesh.h5         1.3 MB
```
```
Download 2 files (2.0 GB) from Odo into /mnt/data/output/transfers/odo/:
  /lustre/orion/abc/proj-shared/a.nc   1.5 GB
  /lustre/orion/abc/proj-shared/b.nc   0.5 GB   changed on Odo, replaces your local copy
```

**UI: `ElicitationModal` with no properties.** When `requested_schema.properties` is empty or
absent, the modal renders the message and two buttons, Approve (accept with `{}`) and Decline,
with no RJSF form and no Cancel button. Closing the backdrop still sends `cancel`. Forms with
fields render as before.

## Risks / Trade-offs

- [A parent folder with very many entries makes its `ls` slow, since the client pages through
  every entry to find a few names] → Acceptable for this change, because the files are
  usually in small run folders. See Open Questions for a filter.
- [A file the identity can read in a folder it cannot list (`--x` permission) can no longer be
  downloaded, though the HTTPS `GET` alone would work] → On a permission error from a folder's
  `ls`, fall back to Transfer `stat` per file in that folder, which needs only search permission.
- [Prompt fatigue in a session that moves many files] → Per call by decision. An agent should
  batch paths into one call, and the tool descriptions say so.
- [Whole-second mtime misses two writes in the same second with the same size] → Negligible for
  research data, and it matches what Transfer reports.
- [Sandbox tools that rewrite files (formatters, `touch`) trigger a re-fetch] → Asked first, so
  it can't happen silently.

## Migration Plan

None. The transfer tools have not been released (!169 is unmerged). Local copies saved by
earlier test builds have a local mtime, so the first download after this change re-fetches them
once, with approval.

## Open Questions

- Whether Transfer `ls`'s `filter` parameter can match several exact names in one call. That
  would cut large-folder listings to the requested files. It's an optimisation that changes no
  behaviour.
