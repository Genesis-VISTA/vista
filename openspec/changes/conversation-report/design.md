## Context

See proposal.md — Why. Requirements are in `specs/conversation-report/spec.md`.

Current pieces this builds on:

- **Skill drafting** — `agents/skill_authoring.py` runs a one-off PydanticAI
  `Agent(model=build_model_for(user), system_prompt=..., output_type=SkillDraft)`
  over the chat's `message_history`, exposed as `POST /skills/generate`
  (unscoped, persists nothing). The UI (`openSaveAsSkill` in `ui/app/page.tsx`)
  opens `SkillEditorModal` in a drafting state and fills it when the draft
  returns. The `hint` parameter exists but the UI never sends one.
- **Per-user project files** — `ProjectAgent` keeps files under
  `data/volumes/<project-id>-<user-id>/`; the sandbox mounts that at `/mnt`, so
  uploads are `/mnt/data/uploads/` to the agent. `services/files.py` resolves the
  directory through `project_agent_pool` and sanitizes names; the Datasets page
  lists `uploads` recursively, so a `reports/` subfolder shows up with no UI work.
- **File URLs** — the browser fetches sandbox files through
  `/api/files/{uploads|outputs}/<path>?project_name=<name>` (the same map the
  agent's `display_file` uses in `agents/agents.py`).
- **Prompt** — `system_prompt()` in `agents/agents.py` is
  `base_system_prompt.md` + project info + KBs + skills. Nothing mentions uploads.
- An open conversation always has `activeChatSessionId` in the UI.

## Goals / Non-Goals

**Goals:**
- Reuse the skill-drafting pattern and the skill editor with no behavioral change
  to either.
- Keep all storage in the existing uploads directory; no DB schema change.

**Non-Goals:**
- A generic markdown-document editor, report versioning, or report history.
- Changing the unscoped `POST /skills/generate` contract.

## Decisions

**1. A separate drafting module, not a mode on the skill drafter.**
`agents/report_authoring.py` mirrors `skill_authoring.py` with its own system
prompt and output model `ConversationReport { title, slug_suggestion, summary,
body }`. The prompt asks for: a short readable summary (goal, approach, key
results, next steps), then `## Record` with exact values, paths, job IDs,
parameters and failures; outputs referenced by their `/mnt/data/output/...`
path, images as markdown images. *Alternative:* one prompt producing report and
skill together — rejected in grilling; the two want different shapes, and most
users won't make a skill.

**2. Project-scoped endpoints in a new `api/reports.py`.**
- `POST /projects/{name}/reports/generate` — body `{message_history, hint?}`,
  returns `ConversationReport`, persists nothing.
- `POST /projects/{name}/reports` — body `{title, summary, slug, body,
  chat_session_id}`, returns `{path}` (relative to uploads, e.g.
  `reports/eutectic-sweep.md`).
Saving needs the project and user to find the uploads directory, and scoping
both under the project keeps the UI proxy in one place
(`ui/app/api/projects/[name]/reports/...`). Drafting doesn't strictly need the
project, but project access is checked anyway, which is harmless.

**3. Save logic in a new `services/reports.py`, reusing `services/files.py`.**
Resolve `uploads_dir` exactly as `save_uploads` does (project lookup →
`get_project_agent_key` → `project_agent_pool.get`). Write the header with
`yaml.safe_dump` (PyYAML is already a dependency) so titles with colons or
quotes can't break it. For one-report-per-chat, scan `uploads/reports/*.md`,
parse each header, and overwrite the first whose `chat_session_id` matches;
otherwise write `reports/<sanitized slug>.md` with `write_file_unique`, which
also handles slug collisions with another chat's report. A linear scan is fine:
reports are small and few per user per project. *Alternative:* a DB table mapping
session → path — rejected; the header already holds the link, and a deleted file
correctly means "no report".

**4. Image and link rewriting in the modal, not in the stored file.**
The stored report keeps `/mnt/data/...` paths so a later agent can open them. The
modal passes a `urlTransform` to `ReactMarkdown` that maps
`/mnt/data/output/<p>` → `/api/files/outputs/<p>?project_name=…` and
`/mnt/data/uploads/<p>` → `/api/files/uploads/<p>?project_name=…`, leaving other
URLs to the default transform. It lives in a small `ui/lib` helper so it can be
unit-tested.

**5. A new `ReportModal` component; `SkillEditorModal` untouched.**
States: drafting → view (rendered) ⇄ edit (textarea) → saving. Actions:
Regenerate with focus… (inline text input + button), Copy markdown, Save to
project (shows the saved path), Save as skill. `page.tsx` swaps the header
button to `openGenerateReport()`, and `openSaveAsSkill` gains an optional `hint`
argument. "Save as skill" closes the report modal and calls
`openSaveAsSkill(reportBody)`, so the skill path is exactly today's flow plus a
hint.

**6. Prompt pointer in `base_system_prompt.md`.** Two sentences naming
`/mnt/data/uploads/` and `/mnt/data/uploads/reports/` and telling the agent to
check there when the user refers to earlier work or a report. It's static text,
so there's no per-chat cost and nothing to cache.

## Risks / Trade-offs

- [Very long chats exceed the model's context and drafting fails] → Same as
  skills today; show the error. History trimming is a noted follow-up.
- [A report used as a skill hint is long] → Acceptable; the hint is appended to
  the user prompt and the model already reads the whole history.
- [Regenerating discards hand edits] → Stated in the spec; the Regenerate
  control sits next to the editor, so this is visible.
- [Linked outputs are later deleted] → The link breaks in the modal and for the
  agent; accepted in grilling (link, don't copy).
- [Header scan picks up a hand-written `.md` with a matching id] → Only files
  under `reports/` with a parseable header are considered; malformed headers are
  skipped.

## Migration Plan

None. Existing skills and uploads are unaffected. Rollback is reverting the
change; saved reports remain as ordinary uploads.
