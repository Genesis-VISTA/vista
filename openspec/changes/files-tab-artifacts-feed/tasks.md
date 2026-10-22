## 1. Safe file serving

- [ ] 1.1 In `backend/src/vista_backend/api/files.py`, add `?preview=1` to the file GET: `inline` only for `png, jpg, jpeg, gif, webp, pdf` (by extension), `attachment` otherwise. Add `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox; default-src 'none'` to every file response. Verify in a new `backend/tests/test_files_api.py`: PNG and PDF previews are inline; `.html` and `.svg` are attachments with or without `preview`; both headers are on every response; path traversal is still 404
- [ ] 1.2 In `ui/app/api/files/[kind]/[...name]/route.ts`, forward `preview` upstream and pass both safety headers through, setting them when the upstream response lacks them. Verify with a new `ui/tests/files-route.test.ts` against a stubbed upstream: the query is forwarded, the headers are always present, and the disposition passes through

## 2. Record of displayed files

- [ ] 2.1 Add `DisplayedFileTable` (`displayed_file`) to `backend/src/vista_backend/db/schemas.py` with the columns and indexes in design.md §2, and FKs cascading on project and chat. Verify in `backend/tests/test_chat_sessions.py` that `init_db` creates it on an existing DB, and that deleting a chat deletes its rows while its files stay on disk
- [ ] 2.2 Add a sandbox-path → `(kind, path)` mapper for `/mnt/data/output/` and `/mnt/data/uploads/` that accepts the URI forms `resolve_uri` accepts. Verify with unit cases that mirror `mcp_servers/vista_mcp_server/tests/test_display_file.py` (bare path, `file://`, `file://localhost`, percent-encoding, `..` refused, other prefixes → none)
- [ ] 2.3 In `ChatRegistry._drive` (`services/chat_run.py`), pair `display_file` calls with their results by `tool_call_id` (name matched after the last `__`). On a result with no `error`, stat the host file and hold a pending record; write pending records in the run's common tail. Verify in `backend/tests/test_chat_run.py`, with the `fake_mcp` `display_file` stub and a `FunctionModel`: a successful display records chat, run, tool call, kind, path, size and `mtime_ns`; an `{error}` result records nothing; a stopped run still writes what it displayed; a file overwritten later in the same run reads as `changed`
- [ ] 2.4 Add the status function (`current` / `changed` / `missing` from size and `mtime_ns`) and `GET /projects/{p}/chat-sessions/{id}/displayed-files` (newest first, with `name`, `mime_type` and `status`). Verify in `backend/tests/test_chat_run_api.py`: order, each status, a chat from another project → 404, and no rows for a chat that displayed nothing
- [ ] 2.5 Add `shown_in` to `FileInfo` in `services/files.py`, filled from the newest record for the path only when it is `current`, with the chat title read at listing time. Verify in `test_files_api.py`: unchanged → `shown_in` set; overwritten → null; displayed in two chats → the newer; never displayed → null

## 3. Agent guidance

- [ ] 3.1 Edit `run_bash`'s description in `mcp_servers/dev_mcp_server/src/dev_mcp_server/server.py` (drop the "automatically displayed" line and the salt-analysis key paths; point to `display_file`), `display_file`'s description in `mcp_servers/vista_mcp_server/src/vista_mcp_server/display_file_mcp.py`, and `backend/src/vista_backend/agents/base_system_prompt.md:4`, as design.md §9 says. Verify that `grep -rn "automatically displayed" mcp_servers backend/src` finds nothing, and that `./scripts/ci-local.sh mcp test` and `backend/tests/test_skills_prompt.py` pass

## 4. Shared preview component

- [ ] 4.1 Add `ui/lib/csv-preview.ts` (quoted fields, CRLF, TSV, first 500 rows plus an omitted count) and a capped stream reader (1 MiB, then cancel). Verify with `ui/tests/csv-preview.test.ts` and `ui/tests/capped-read.test.ts`
- [ ] 4.2 Add `ui/components/FilePreview.tsx` with the renderers in design.md §5 (`<img>` for images including SVG, text/JSON, CSV table, markdown with no raw HTML and images as links, HTML through `SandboxedHtmlCard`, PDF as "Open PDF", otherwise download only). Verify with `ui/tests/FilePreview.test.tsx`: each type picks its renderer; a `<script>` in HTML and markdown is not executed or rendered; a markdown image renders as a link; the truncation note appears past the cap

## 5. Files page

- [ ] 5.1 Read the Next docs in `ui/node_modules/next/dist/docs/` on `useSearchParams` and Suspense. Then replace `ui/app/datasets/` with `ui/app/files/page.tsx`: Outputs and Uploads trees, each labelled with its sandbox path; upload, drag-drop, download and delete kept; the catalog dropdown gone; `FilePreview` beside the tree; selection in `?kind=&path=`; "Shown in <chat>" linking to `/?chat=&run=&step=`. Rename the NavRail entry and icon and the `project-stats` field and label. Verify with `cd ui && npm run lint` and `npm test`
- [ ] 5.2 Reload the listing when any chat in the project leaves `working`, through `useChatRunStatus`. Verify with a unit test using `resetChatRunStatusForTests` and a stubbed fetch
- [ ] 5.3 Update `ui/e2e-hermetic/shell.spec.ts`, `project-context.spec.ts` and `ui/tests/projects-create-opens-chat.test.tsx` from datasets to files. Add a hermetic spec `ui/e2e-hermetic/files.spec.ts`: the trees render from fixture data, `?kind=outputs&path=run1/plot.png` opens that file with its preview, and "Shown in" links to the chat URL. Verify with `cd ui && npm run test:e2e:hermetic`

## 6. Artifacts feed and opening a chat at a turn

- [ ] 6.1 Add the proxy route and a `ui/lib/displayed-files.ts` client for `displayed-files`, and add an optional `tool_call_id` to the `function_tool_call` step bubble in `ui/lib/run-renderer.ts`. Verify with new cases in `ui/tests/run-renderer.test.ts`: the step bubble carries `tool_call_id`, and a successful `display_file` result yields a provisional feed item
- [ ] 6.2 Replace the single `latestResult.ui.kind === "file"` block in `ui/app/page.tsx`'s Artifacts panel with the feed (design.md §7): fetch on open, on chat switch and on `run_finished`; provisional items during a run; image cards and expandable rows; changed and missing states; "Open in Files"; a jump to the turn. HTML results, the prediction summary and references keep rendering from `latestResult` above it. Verify in `ui/e2e-hermetic/workspace.spec.ts` with fixtures: three displays list newest first and survive a reload, a changed item shows "file has changed on disk" with no preview, and a missing item shows "file no longer exists" with no Files link
- [ ] 6.3 Give bubbles `id="msg-<id>"` and read `chat` / `run` / `step` on load (design.md §8): select the chat, scroll to and highlight the turn's answer, select Activity and scroll to the step, then clear the params with `router.replace`. Handle an unknown run (open at the end) and an unknown chat ("Conversation not found"). Verify in `ui/e2e-hermetic/chat.spec.ts` with fixtures for each of the three cases, and a bubble from before this change (no `tool_call_id`) that falls back to the turn

## 7. Verification

- [ ] 7.1 Run `./scripts/ci-local.sh lint` and `./scripts/ci-local.sh backend ui mcp test`; all pass
- [ ] 7.2 Manual (not PR CI; needs a live provider and the sandbox): `./launch.sh logs`. Ask the agent to plot something and confirm it calls `display_file`, the feed shows it, and Files shows it under Outputs with "Shown in". Re-run the plot and confirm the earlier item reads "file has changed on disk". Follow "Shown in" from Files and land on the turn and its Activity step. Preview a log, a CSV, a markdown file, an HTML file containing a `<script>` (it must not run) and a PDF
- [ ] 7.3 Run `openspec validate files-tab-artifacts-feed --strict`; it passes
