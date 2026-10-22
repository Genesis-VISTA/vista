## Context

Current state that shapes the approach (motivation: proposal.md):

- **Files API.** `api/files.py` / `services/files.py` already list both kinds recursively
  (`rglob`, nested `name`s) and serve every file as `attachment`. The UI proxies them through
  `ui/app/api/files/[kind]/…`, which forwards only `content-type` and `content-disposition`.
  `api/uploads.py` is not mounted and is left alone.
- **`display_file`** is an MCP tool on `vista_mcp_server` (`display_file_mcp.py`). It takes a
  sandbox path, resolves it through the `uri_map` the backend puts in each call's metadata
  (`agents.py:768`), and returns `{uri, mime_type, filename}` on success or `{error}` on
  failure. It never raises for a bad path.
- **Chat runs.** A turn is a `ChatRun` driven by `ChatRegistry._drive`
  (`services/chat_run.py:247`). That is the only place where `run_id`, the chat and the
  agent (with `output_dir` / `uploads_dir`) are all in reach, and it sees every
  `FunctionToolCallEvent` / `FunctionToolResultEvent`. Its common tail runs for done, failed,
  stopped and interrupted runs alike. `run_id` is not a column: it lives on the UI's persisted
  bubbles (`messages[].run_id`), including the `intermediate` step bubbles the Activity tab
  is built from.
- **Concurrency.** Every chat in a project mounts the same output folder, and two chats can
  run at once. This is why provenance is limited to displayed files (proposal, Non-goals).
- **Chat deletion** is a plain `session.delete(row)`, with SQLite foreign keys on per
  connection (`db/db.py:77`), so `ondelete="CASCADE"` is honoured.
- **Schema.** `init_db` creates new tables; there is no migration tool.
- **Chat selection** lives in localStorage per project (`ui/lib/chat-session.ts`). Bubbles
  render with no DOM id, and nothing reads the URL.
- **Run status** for every chat in a project is already polled by `useChatRunStatus`
  (`ui/lib/chat-run-status.ts`, 3 s while visible).
- **PDFs.** Electron answers an inline PDF frame with `204` and opens it in the system
  browser while the window is unsandboxed (`electron/src/main.js`, `sendPdfsToBrowser`).

## Goals / Non-Goals

**Goals:** one shared preview component for the Files page and the feed; one record of
displayed files that both read; and file serving that is safe by construction rather than
by each caller remembering.

**Non-Goals:** in addition to the proposal's, no change to how `display_file` resolves
paths, no new MCP metadata, and no change to the Activity tab's content.

## Decisions

### 1. Record displayed files in the chat run driver

`_drive` keeps, per run, the `display_file` calls it has seen (by `tool_call_id`, with the
`uri` argument). When the matching result arrives and carries no `error`, it maps the sandbox
path to `(kind, path)` with the same two prefixes the `uri_map` uses (`/mnt/data/output/`
→ outputs, `/mnt/data/uploads/` → uploads), stats the host file under the agent's
`output_dir` / `uploads_dir`, and holds a pending record with size and `st_mtime_ns` taken
**at that moment**. The tool is matched by its name after the last `__`, as the UI's labels
are. Paths outside the two prefixes, or files that are gone by the time the result arrives,
are not recorded.

Pending records are written in the run's common tail, in the same place `_write_row` saves
the run, so the record exists for done, failed, stopped and interrupted runs. Nothing is
written mid-run, because the run already holds a DB session open across the stream and
writes from inside open transactions have deadlocked before (the forum outbox). The stat is
taken at display time, so a later tool in the same run that overwrites the file correctly
leaves the record "changed".

*Alternatives:*
- **In the MCP server.** Rejected: it doesn't know the chat or the run, and it would have to
  write VISTA's DB.
- **In `ProjectAgent.run_stream`.** Rejected: it has no `run_id`. It would also record the
  stateless `/agent` path, which has no chat to link to.
- **Parsing the returned UI URL.** Rejected: it couples the record to the UI's route shape.
  The call's own sandbox path is the source.
- **On the chat bubble.** Decided against in the grilling session: the Files page would have
  to scan every chat's JSON to answer "shown in".

### 2. Table `displayed_file`

| Column | Notes |
|---|---|
| `id` | UUID primary key |
| `project_id` | FK `project.id`, `ondelete=CASCADE` |
| `chat_session_id` | FK `chat_session.id`, `ondelete=CASCADE` (the chat goes, its links go) |
| `run_id` | `ChatRun.run_id` hex, as on the bubbles |
| `tool_call_id` | the `display_file` call |
| `kind`, `path` | `uploads` / `outputs`, relative POSIX path as the files API names it |
| `shown_at` | ISO timestamp, as other tables use |
| `size`, `mtime_ns` | at display time |

The table is indexed on `(project_id, kind, path)` for "shown in" and `(chat_session_id,
shown_at)` for the feed. Every display adds a row, with no upsert: the feed shows each
display, and "shown in" takes the newest row for a path.

### 3. Change status is computed when read, from size and `mtime_ns`

A small service function stats each record's file and returns `current`, `changed` or
`missing`. There is no hashing (settled in the grilling session). `mtime_ns` rather than
float seconds avoids a rounding mismatch on filesystems with sub-second times.

### 4. API

- `GET /projects/{p}/chat-sessions/{id}/displayed-files` lists the chat's records newest
  first: `{kind, path, name, mime_type, run_id, tool_call_id, shown_at, status}`. The
  `mime_type` comes from the extension, the same way `display_file` derives it.
- **The file list** (`FileInfo`) gains `shown_in: {chat_session_id, chat_title, run_id,
  tool_call_id} | null`, filled only when the newest record for that path is `current`.
  This is one query per listing, joined in memory with the stat results the listing already
  has.
- **File GET** takes `?preview=1`. It is `inline` only when the extension is in the
  allowlist (`png, jpg, jpeg, gif, webp, pdf`); everything else stays `attachment`. **Every**
  file response, preview or not, carries `X-Content-Type-Options: nosniff` and
  `Content-Security-Policy: sandbox; default-src 'none'`, so even a mistaken inline response
  can't run script on VISTA's origin. The type is decided from the extension, never from the
  content.
- **The Next proxy routes** forward `preview`, and forward the two safety headers instead of
  dropping them. If the upstream response lacks them, they set the same values themselves.
  The new endpoint gets a proxy route beside the chat-session ones.

*Alternative:* a separate `/preview` route. Rejected: one GET with a flag keeps one
path-validation path (`_get_file`).

### 5. One `FilePreview` component

`ui/components/FilePreview.tsx` takes `{kind, path, name, size, projectName}` and picks a
renderer by extension:

- **Images** (including SVG): `<img src=…?preview=1>`. `<img>` ignores disposition and runs
  no SVG script. The lightbox reuses `ArtifactImage`.
- **Text, logs, code, JSON:** `fetch`, reading the body stream up to 1 MiB and then cancelling
  it. It's shown in a `<pre>`, with JSON pretty-printed when it parses whole, and a truncation
  note compares the bytes read with the listed `size`. No backend range support is needed.
- **CSV/TSV:** the same capped read, parsed by a small quoted-field parser in `ui/lib`
  (no new dependency), showing the first 500 rows and a count of rows omitted.
- **Markdown:** `react-markdown` with `remark-gfm`, as chat uses it. No `rehype-raw`, so raw
  HTML stays text. An image inside a previewed markdown file renders as a link, not an
  image, so a project file can't make VISTA fetch a remote URL by being looked at.
- **HTML:** the capped text goes through the existing `SandboxedHtmlCard` (DOMPurify plus
  `sandbox=""` plus CSP).
- **PDF:** an "Open PDF" button that opens `…?preview=1` in a new window. Electron then shows
  it in a sandboxed child window, or sends it to the system browser while unsandboxed. An
  inline `<iframe>` was rejected: unsandboxed, Electron's 204 would leave a blank frame.
- **Anything else:** "No preview for this type" and Download.

### 6. Files page

- `ui/app/datasets/` is replaced by `ui/app/files/`, and `NavRail`'s entry becomes "Files"
  with a folder icon. `project-stats` renames `datasets` to `files`.
- The page builds two trees from the nested `name`s (folders first, then files newest first)
  with a preview pane beside them, stacking on narrow widths.
- **Selection** is URL state: `/files?kind=outputs&path=run1/plot.png`. That is what "Open in
  Files" links to, and it expands the file's folders. Per AGENTS.md, read the Next 16 docs in
  `ui/node_modules/next/dist/docs/` for `useSearchParams` and its Suspense requirement before
  writing it.
- **Refresh:** the page subscribes to `useChatRunStatus` and reloads the listing when any
  chat in the project leaves `working`. That is the same poll the nav rail already runs, so
  no new endpoint is needed.
- **"Shown in"** renders as a link to `/?chat=<id>&run=<run_id>&step=<tool_call_id>`.

### 7. Artifacts feed

- The Artifacts tab fetches `displayed-files` for the open chat on open, on chat switch and
  on `run_finished`.
- **During a run**, a successful `display_file` result already reaches `run-renderer.ts`. It
  prepends a provisional item, which the refetch on `run_finished` replaces with the
  recorded one.
- **Items:**
  - `current` images render inline;
  - `current` other types render a row that expands into `FilePreview`;
  - `changed` shows the name and "file has changed on disk";
  - `missing` shows the name and "file no longer exists", without a Files link.
- **Non-file results** (HTML `ui`, prediction summary, references) keep coming from
  `latestResult` and render above the feed, as today.

### 8. Opening a chat at a turn

- **Reading the URL.** The chat page reads `chat`, `run` and `step` once on load. A `chat`
  that belongs to the active project becomes the selected chat, written to localStorage as
  selecting it would be. Then the URL params are cleared with `router.replace`, so a reload
  doesn't re-jump.
- **Highlighting the turn.** Bubbles get `id="msg-<id>"`. The jump scrolls to and briefly
  highlights the turn's final answer (the last non-intermediate bubble with that `run_id`),
  and selects Activity.
- **Finding the step.** Activity scrolls to its step for `step`. That needs the step bubble
  to carry the `tool_call_id`, so `run-renderer.ts` adds an optional `tool_call_id` to the
  `function_tool_call` step bubble it already persists. Bubbles from before this change lack
  it, and the jump falls back to the turn only.
- **Fallbacks.** An unknown `run` opens the chat at its end. An unknown `chat` shows a one-line
  "Conversation not found" note and behaves as a plain `/`.

### 9. Agent guidance

- **`run_bash`'s description:** drop "When a plot is saved, it is automatically displayed to
  the user" and the salt-analysis "Key paths" block, and say to call `display_file` to show a
  file.
- **`display_file`'s description:** say that this is the only way to show a file, and that
  markdown images of sandbox paths do not render.
- **`base_system_prompt.md:4`:** say the same, for any file (not just plots).

## Risks / Trade-offs

- **[A file overwritten with identical size within the same `mtime_ns`]** is reported
  `current`. → Accepted. That takes a sub-nanosecond-resolution rewrite of the same length,
  and hashing was explicitly dropped.
- **[A backend crash mid-run loses that run's records]**, because they are written in the
  tail. → Accepted. The run is already marked interrupted at the next start, and its files
  remain on the Files page with no link.
- **[A `uri` argument in a form `display_file` accepts but our prefix mapping doesn't]**,
  e.g. a `file://localhost/…` URI. → The mapper uses the same normalisation as
  `resolve_uri`, and a test feeds it the forms `test_display_file.py` covers.
- **[Stat-walking many records on every feed load]** → One `stat` per record. A chat displays
  tens of files, not thousands. No caching until measured.
- **[`chat_title` in `shown_in` goes stale after a rename]** → It is read at listing time,
  not stored.
- **[The safety headers break an existing consumer]**: chat's current `display_file` image is
  an `<img>` on the same route. → `<img>` is unaffected by `nosniff` with a correct image
  type, or by a document CSP. The download path is unchanged.

## Migration Plan

`init_db` creates `displayed_file` on first start of the new version. Existing chats have no
records, so their files show no provenance and their feed starts empty. Rollback leaves an
unused table behind.
