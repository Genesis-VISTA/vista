## Why

Work item #85, from the Genesis Hackathon feedback slide ("VISTA Next Steps"):

> UX Clarity: "Make agent output location clear (now buried in dataset folder)"

Agent outputs go to `/mnt/data/output` and appear only as a flat list under Datasets, where
they can be downloaded or deleted but not looked at. Chat's Artifacts panel shows only the
latest file the agent displayed, and never says where it lives. A file shown three turns ago
is gone from view, and nothing leads from a file back to the conversation that produced it.

## What Changes

- **Datasets becomes Files** at `/files`. `/datasets` goes away with no redirect: VISTA is a
  desktop app, so there are no stored links to it. The page shows Outputs (`/mnt/data/output`)
  above Uploads (`/mnt/data/uploads`) as folder trees, newest first, refreshed when a chat run
  finishes. Upload, download and delete stay. The placeholder "AmSC Data Catalog" dropdown,
  which does nothing, is removed. The project card counts "files", not "datasets".
- **Files can be previewed**, by type: images, text and logs, CSV as a table, markdown, HTML
  in the existing sandboxed frame, and PDF. Everything else is download-only.
- **Nothing a project's files contain runs outside a sandbox.** Agent-written HTML or SVG is
  never served as a page on VISTA's origin. The file server sends a file inline only for an
  allowlist of image types and PDF, and every response forbids sniffing and carries a
  sandboxing CSP.
- **The Artifacts panel becomes a feed** of every file displayed in the open chat, newest
  first. Images show inline; other types expand in place with the same previews as Files.
  Each item opens the file in Files and jumps to the turn that displayed it.
- **VISTA records each displayed file** against its chat, turn and tool call, with the file's
  size and modification time. The agent never sees this record. A file that has since changed
  on disk or gone shows "file has changed on disk" or "file no longer exists" in the feed,
  without a preview, and the Files page offers "Show in chat" only for a file that is unchanged.
- **Chats can be opened by URL** at a turn (`/?chat=<id>&run=<run_id>`), which highlights
  that turn's answer and scrolls Activity to the step that displayed the file.
- **The agent is told plainly how to show a file.** `run_bash` stops claiming that saved plots
  are displayed automatically (they are not), and drops its hardcoded salt-analysis paths.
  `display_file` and the system prompt say that markdown images of sandbox paths do not
  render and that `display_file` is how a file is shown. Chat does not rewrite such images.

## Non-goals

- Provenance for files that were written but never displayed (by `run_bash`, skill scripts,
  campaign or debate HPC fetches, reports, uploads). Attributing `run_bash` writes needs a
  folder diff around each call, and concurrent chats share one output folder; that is its own
  change.
- Backfilling records for files displayed before this change. They show no provenance.
- Content hashing. Change is detected by size and modification time only.
- `/mnt/data/work` (fetched repositories) and remote HPC output directories.
- Live tailing of log files.

## Capabilities

### New Capabilities

- `project-files`: the Files page, typed previews, the rule that a project's files never run
  outside a sandbox, the record of displayed files with change detection, and the agent's
  guidance for showing a file.

### Modified Capabilities

- `ui-agent-activity`: the Artifacts tab is a feed of the chat's displayed files, and a turn
  can be opened by URL with its answer and Activity step highlighted.
- `ui-shell`: the datasets page is the files page in the shell, project-switching and
  project-required scenarios.

## Impact

- **DB:** one new table of displayed-file records, cascading on chat and project deletion;
  created by `init_db`, no migration.
- **Backend:** chat run driver (`services/chat_run.py`) records `display_file` returns; files
  API and service gain preview mode, safety headers and "shown in"; a chat-session endpoint
  lists its displayed files.
- **MCP:** `display_file` and `run_bash` tool descriptions; `base_system_prompt.md`.
- **UI:** `ui/app/datasets` replaced by `ui/app/files`; `NavRail`, `project-stats`, the files
  API routes, and the chat page's Artifacts panel and conversation (deep link, highlight).
- **Electron:** none; PDFs follow the existing sandbox rule in `electron/src/main.js`.
