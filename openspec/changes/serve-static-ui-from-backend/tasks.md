## 1. Precondition

- [ ] 1.1 Archive `skill-hpc-jobs` (`/opsx:archive skill-hpc-jobs`) so its spec is merged before its proxy mapping moves; verify `openspec list` no longer shows it

## 2. Backend: `/api` on one origin

- [ ] 2.1 Mount every router in `backend/src/vista_backend/api/api.py` with `prefix="/api"` (PALISADE included), move `openapi_url`/`docs_url`/`redoc_url` under `/api`, and update backend test request paths; verify `cd backend && uv run --extra dev pytest` passes and `/api/openapi.json` lists only `/api/...` paths
- [ ] 2.2 Add the catch-all `/api/{path:path}` JSON 404 (design D2); verify a new pytest that `GET /api/no-such-thing` is 404 `application/json`
- [ ] 2.3 Add `Settings.ui_dir` (`VISTA_BACKEND_UI_DIR`) and the static mount with the D3 lookup and the D4 API-only fallback; verify pytest against a temporary folder: `/` and `/skills` and `/skills/` serve their HTML, an existing `.txt` payload is served as a file, `/no-such-page` is 404 with `404.html`, assets load without a user, and with the setting unset or pointing at a missing folder `/` is 404 and the startup log names the reason
- [ ] 2.4 Change the default ports: `Settings.port` 38417, `Settings.mcp_url` `http://localhost:38418/mcp`, and `mcp_servers/vista_mcp_server/src/vista_mcp_server/server.py` `--port` 38418; verify the backend and MCP pytest suites pass and `grep -rn "8001\|8000" backend/src mcp_servers/*/src` finds no default
- [ ] 2.5 Change `uri_map` in `backend/src/vista_backend/agents/agents.py` to `/api/projects/{name}/{outputs|uploads}/{path}` with the name URL-encoded; verify a pytest that a project name with a space yields the encoded form

## 3. Backend: logic moved out of the proxies

- [ ] 3.1 Write the route-by-route table (each `ui/app/api/**/route.ts` → its backend endpoint, plus any reshaping or validation it does) as a checklist in this change's `handoff.md`; verify every one of the 40 route files appears
- [ ] 3.2 Port `mcp/call`'s `CallToolResult` → `ExecutionResult` normalisation (envelope, HTML/image extraction, `display_file` detection) to the backend MCP call endpoint with a Pydantic response model; verify pytest cases mirroring each branch of the TS (text, error, HTML resource, image, `display_file` structured content, wrapped `result`)
- [ ] 3.3 Port `mcp/tools`' shaping to its backend endpoint; verify a pytest that the response matches what the proxy returned for the same upstream data
- [ ] 3.4 Enforce the `files` route's path-segment rules (no empty, `.` or `..`) in the backend files endpoints; verify pytest that each rejected form returns 400 and a nested path still works
- [ ] 3.5 Move the skills shaping (`hpc_jobs` → `hpcJobs`, and whatever 3.1 lists for `skills/[slug]`, `skills/import`, `skills/import/upload`, `skills/generate`, `projects/[name]/skills/from-output`) into the backend; verify pytest for each shape
- [ ] 3.6 Close the remaining rows of 3.1 (including `settings-modal-revamp`'s `/users/me/inference`), confirming each backend endpoint accepts the request exactly as the UI will send it; verify every row in `handoff.md` is ticked with its pytest or "pure forward, none needed"

## 4. UI: static export and direct calls

- [ ] 4.1 `ui/next.config.mjs`: `output: 'export'` for builds, dev-only `/api/:path*` rewrites to `VISTA_BACKEND_URL` keyed on the dev-server phase (D5), drop `standalone` and `serverExternalPackages`; read `ui/node_modules/next/dist/docs/01-app/02-guides/static-exports.md` first; verify `npm run build` writes `ui/out` with every page listed in the single-origin-serving spec (fix any Suspense-boundary build errors here)
- [ ] 4.2 Add `ui/lib/api.ts` (`api`, `projectApi`, D7); verify vitest for prefixing and project-name encoding
- [ ] 4.3 Re-point the chat run, run-events, stop, status and elicitation calls through the helper; verify manually, **not in PR CI**: with `./launch.sh logs` running, a chat run's events appear incrementally through `next dev`'s rewrite. **If they arrive only when the stream ends, stop and raise it**: the fallback is an open decision (proposal)
- [ ] 4.4 Re-point every remaining `/api/...` call site to the backend paths through the helper, then delete `ui/app/api/`; verify `grep -rn "\"/api\|'/api\|\`/api" ui/app ui/components ui/lib` finds only `lib/api.ts`, and `npm run lint` passes
- [ ] 4.5 Update `ui/lib/file-links.ts` and the report-link handling to the new file-link form only; verify their vitest tests with new-form fixtures
- [ ] 4.6 Delete `ui/tests/chat-run-routes.test.ts`; verify `npx vitest run` passes
- [ ] 4.7 Add `ui/scripts/serve-static.mjs` (D11), switch `playwright.hermetic.config.ts` and `playwright.shots.config.ts` to `next build && node scripts/serve-static.mjs`, and update `e2e-hermetic/stub.ts` keys; verify `./scripts/ci-local.sh ui test` (both suites) passes
- [ ] 4.8 Verify manually against the backend serving `ui/out` (`VISTA_BACKEND_UI_DIR=ui/out`): navigate to every page through the nav rail, reload on `/knowledge-bases/project`, run a chat that shows a figure, and confirm the network panel shows no request to another origin or a 404 on any `.txt` payload

## 5. Launch, startup protocol, packaging

- [ ] 5.1 `scripts/launch.sh`, `scripts/mac_dev_launcher.sh`, `scripts/nightly-validation.sh`: new ports, health waits on `/api/openapi.json`, `next dev` unchanged on `:3000`; verify `./launch.sh logs` opens a working window and `./launch.sh logs --no-electron` serves `localhost:3000`
- [ ] 5.2 Startup protocol v2 (D8) in `electron/src/startup-protocol.js`: drop the `ui` phase (and any failure code only it emitted), require `url` on `backend` ready, accept only protocol 2; verify `cd electron && npm test`, including the launcher/message cross-check
- [ ] 5.3 `scripts/package_launcher.sh` and `.ps1`: new ports, no `VISTA_UI_PORT`, no `node/bin`, no `ui` phase, export `VISTA_BACKEND_UI_DIR`, emit protocol 2 with `url` on backend ready; verify `./scripts/ci-local.sh launcher`
- [ ] 5.4 `scripts/build_local_package.sh` and `scripts/build.sh`: stage `ui/out`, delete `bundle_node`, the Node download and the Node manifest entries; `scripts/smoke_test_package.sh`: check `/` and `/api/projects` on the backend port; verify manually, **not in PR CI**: a local macOS package build passes its own smoke test and supervised-protocol check, and the package contains no `node` binary
- [ ] 5.5 Update the Electron comments citing `:3000` (`main.js`, `routing.js`, `permissions.js`); verify `./scripts/ci-local.sh electron`

## 6. Screencast toolkit

- [ ] 6.1 `.claude/skills/vista-screencast/lib/ui.mjs` builds the private copy as an export and returns its `out/`; `lib/server.mjs` serves it with the D3 lookup on non-`/api` paths and answers or forwards `/api`; remove `PORTS.ui`; `lib/session.mjs` defaults to `:38417`; verify the toolkit's own tests (`npm test` in the skill folder)
- [ ] 6.2 Rewrite `selftest/capture/responses.jsonl` paths to the `/api` form; verify the selftest replay produces its video

## 7. Docs and wrap-up

- [ ] 7.1 Update `AGENTS.md` and `docs/` wherever they name the old ports, the UI server, `.next/standalone`, bundled Node or the `/api` proxy (including the smoke-test `curl` lines); verify `grep -rn "8001\|:8000\|standalone\|_backend.ts" AGENTS.md docs` finds nothing stale
- [ ] 7.2 Verify `./scripts/ci-local.sh` (all lint and test) passes and `openspec validate serve-static-ui-from-backend --strict` is clean
