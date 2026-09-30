## Why

The chat header's only way to capture a conversation is "Save as skill", which
turns it into a reusable procedure. Most conversations are not procedures —
they are investigations whose findings (values, job IDs, output files, open
questions) are what a researcher wants to keep and pick up again in a new chat.
Today that record is lost when the chat ends, and the agent has no idea that
anything the user saved into the project exists.

## What Changes

- **BREAKING (UI):** The chat header's "Save as skill" button is replaced by
  **"Generate report"**. There is no longer a direct Save-as-skill shortcut; a
  skill is drafted from inside the report modal instead.
- New report drafting from the conversation, using the same summarization
  approach as skill drafting (the user's configured model, the full message
  history, structured output). A report is a readable summary followed by a
  detailed **Record** section, and links to output files by their sandbox path.
- New report modal: rendered markdown with inline images for linked outputs, a
  hand-edit toggle, **Regenerate with focus…** (a free-text hint),
  **Save to project**, and **Save as skill**.
- **Save to project** writes the report as a markdown file with a small header
  (`title`, `summary`, `date`, `chat_session_id`) into the user's project
  uploads under `reports/`, where it appears on the Datasets page. One report
  per chat: re-saving replaces that chat's report and keeps its filename.
- **Save as skill** drafts a skill from the conversation steered by the edited
  report, then opens the existing skill editor unchanged.
- The agent's base system prompt tells it where the user's uploads and saved
  reports live, so a new chat can find an earlier report when the user refers
  to it.

## Capabilities

### New Capabilities
- `conversation-report`: drafting, editing, saving, and re-using a report of a
  chat conversation, including the report-to-skill path and the agent's
  awareness of saved reports and uploads.

### Modified Capabilities
None — skill drafting and the skill editor keep their existing behavior; no
current spec covers them.

## Impact

- **Backend:** new report-drafting agent module; new project-scoped endpoints to
  draft and to save a report; base system prompt gains an uploads/reports note.
- **UI:** `ui/app/page.tsx` header button and handlers; a new report modal
  component; new Next.js proxy routes for the report endpoints. The existing
  `SkillEditorModal` and `/api/skills/generate` are reused as they are.
- **Storage:** reports live in the existing per-user project uploads directory
  (`data/volumes/<project>-<user>/data/uploads/reports/`); no DB schema change.
- **Tests:** hermetic backend tests with a fake model; UI unit test for the
  modal. No live, HPC, or sandbox tests.

## Non-goals

- Project-shared (cross-member) report storage — VISTA is a local desktop app
  today, so per-user uploads are sufficient.
- Listing reports in the system prompt, an attach-report picker, or an
  agent-side tool for saving reports.
- Trimming long histories before drafting (a follow-up that would also apply to
  skills).
- Any change to skill drafting itself or to VISTAGuard.
