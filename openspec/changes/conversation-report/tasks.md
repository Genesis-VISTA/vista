All tasks are hermetic (PR CI). None need live, HPC, or sandbox markers.

## 1. Report drafting (backend)

- [ ] 1.1 Add `backend/src/vista_backend/agents/report_authoring.py` with `ConversationReport` (`title`, `slug_suggestion`, `summary`, `body`) and `generate_report_draft(message_history, hint, user)` mirroring `skill_authoring.py`; the system prompt asks for a readable summary then `## Record`, with outputs referenced by `/mnt/data/output/...` path. Verify with a new `backend/tests/test_report_authoring.py` that uses a `FunctionModel` (pattern: `backend/tests/test_campaign_driver.py`) and asserts the structured draft is returned and the hint reaches the user prompt.

## 2. Report saving (backend)

- [ ] 2.1 Add `backend/src/vista_backend/services/reports.py`: resolve the user's project uploads dir as `services/files.py:save_uploads` does; write `reports/<slug>.md` with a `yaml.safe_dump` header (`title`, `summary`, `date`, `chat_session_id`) + body; overwrite in place when an existing `reports/*.md` header has the same `chat_session_id` (keeping its filename), otherwise create via `write_file_unique`; sanitize the slug with the existing helper; skip malformed headers; `encoding="utf-8"` everywhere. Verify with unit tests in a new `backend/tests/test_reports.py` covering first save, re-save keeps the filename with a new title, re-save after deletion creates a new file, a slug collision with another chat gets a unique name, an unsafe slug stays inside `reports/`, and a header with `: ` and quotes round-trips.
- [ ] 2.2 Add `backend/src/vista_backend/api/reports.py` with `POST /projects/{name}/reports/generate` and `POST /projects/{name}/reports`, and register it in `api/api.py`. Verify with API tests in `backend/tests/test_reports.py` using the `client` fixture: a non-member gets 403, save returns the relative path, the saved file appears in `GET /projects/{name}/uploads`, and generate returns a draft with the model monkeypatched to a `FunctionModel`.

## 3. Agent pointer (backend)

- [ ] 3.1 Add the two-sentence uploads/reports note to `backend/src/vista_backend/agents/base_system_prompt.md`. Verify with an assertion (in `backend/tests/test_skills_prompt.py` or a new `backend/tests/test_system_prompt.py`) that the built prompt contains `/mnt/data/uploads/` and `/mnt/data/uploads/reports/`; then run `./scripts/ci-local.sh backend` and confirm it passes.

## 4. Report modal (UI)

- [ ] 4.1 Read the Route Handler docs in `ui/node_modules/next/dist/docs/`, then add proxy routes `ui/app/api/projects/[name]/reports/route.ts` and `ui/app/api/projects/[name]/reports/generate/route.ts` modelled on `ui/app/api/skills/generate/route.ts`. Verify with `cd ui && npm run lint` and a manual `curl` against the running stack.
- [ ] 4.2 Add a `ui/lib` helper that maps `/mnt/data/output/<p>` and `/mnt/data/uploads/<p>` to `/api/files/{outputs|uploads}/<p>?project_name=…` for `ReactMarkdown`'s `urlTransform`. Verify with `ui/tests/report-links.test.ts` (both prefixes, URL-encoded project names, other URLs untouched).
- [ ] 4.3 Add `ui/components/ReportModal.tsx`: drafting state, rendered view with inline images via 4.2, edit toggle, "Regenerate with focus…", Copy markdown, Save to project (shows the saved path or an error), and Save as skill. Verify with `ui/tests/ReportModal.test.tsx`: the drafting state renders, an edit changes what `onSave` receives, regenerate passes the hint, an error disables saving, and Save as skill passes the current body.
- [ ] 4.4 In `ui/app/page.tsx`, replace the header "Save as skill" button with "Generate report" (disabled on an empty history), add `openGenerateReport`/regenerate/save handlers that send `activeChatSessionId`, and give `openSaveAsSkill` an optional `hint` used by the report modal's Save as skill. Verify with `./scripts/ci-local.sh ui` (lint and tests pass).

## 5. End-to-end check

- [ ] 5.1 Run `./launch.sh logs` and check with Playwright that a real conversation generates a report with an inline plot, saves it (it appears under `reports/` on the Datasets page), re-saves under the same filename, opens the skill editor via Save as skill, and that a new chat asked about "my last report" finds and reads it. This is a manual check that needs an inference key, so keep it out of PR CI.
