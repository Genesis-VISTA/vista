## 1. Backend: version and settings

- [ ] 1.1 Add `update_checks` (default true), `update_prereleases` (default false) and `update_checked_at` (nullable) to `LocalSettingsTable` with server defaults, and the first two to `LocalSettingsUpdate` / `LocalSettingsPublic`. Verify: `db/schema_check.py` accepts a DB created before the change (add a case to the existing schema-check tests), and a `PUT /settings` round-trip test passes
- [ ] 1.2 Add `Settings.commit` (from `VISTA_COMMIT`, which the launchers export beside `VISTA_VERSION` from the manifest's commit) and `Settings.updates.github_api` (default `https://api.github.com`). Verify: `scripts/package_launcher.sh` / `.ps1` export it, and `./scripts/ci-local.sh launcher` passes
- [ ] 1.3 Add `GET /version` returning version, commit and `checks: dev_build` for `dev` / `+meta` / `-dirty`. Verify: `backend/tests/test_version_api.py` covers a release version, `dev` and `0.2.4+3.gabc`

## 2. Backend: update check

- [ ] 2.1 Add `packaging` as an explicit backend dependency (`uv add packaging`) and `services/update_check.py`: tag normalisation, semver comparison, stable via `releases/latest`, prereleases via `/releases` with drafts excluded, `update.json` lookup for `data_reset`. Verify: `backend/tests/test_update_check.py` with a fake fetch covers `0.2.3`→`v0.2.5` update, equal versions, `0.2.2-rc1`→`v0.2.2` update, an RC hidden unless opted in, missing `update.json` → `data_reset: null`, and a fetch that raises, returns 403/429, or returns bad JSON → no update and a log line
- [ ] 2.2 Run the check from the lifespan: on start, then 24h after the last success (retry hourly on failure), skipped when checks are off or the build is a dev build; persist `update_checked_at`. `POST /updates/check` runs it now. Verify: a test with a fake clock and a counting fake fetch shows no request while off and exactly one per period while on
- [ ] 2.3 Extend `GET /version` with `latest`, `notes_url`, `install_command` (the release's own one-liner for this platform), `checked_at` and `data_reset`. Verify: API test against a fake GitHub served by `respx` or an in-process app on `updates.github_api`

## 3. Backend: download, verification and activity

- [ ] 3.1 Add `services/update_download.py`: free-space check, streamed download to `VISTA_HOME/updates/<version>/*.part`, sha256 against the API `digest` (refuse an asset without one), archive also against its `.sha256`, `ready.json`, progress state; `POST` / `GET /updates/download`. Verify: `backend/tests/test_update_download.py` covers success, a tampered byte (file deleted, `failed` with reason), a missing digest, and too little disk
- [ ] 3.2 Clean the cache on start when the running version equals a cached one. Verify: unit test
- [ ] 3.3 Add `GET /activity` combining `chat_run_registry.active()`, running debate tasks and `campaign_service.list_open_jobs`. Verify: `backend/tests/test_activity_api.py` with one active run, one debate and one open job
- [ ] 3.4 Run `./scripts/ci-local.sh backend` (lint and test) and verify it passes

## 4. UI: About and the marker

- [ ] 4.1 Read the relevant docs in `ui/node_modules/next/dist/docs/`, then add `ui/lib/version.ts` and proxy routes for `/version`, `/updates/*` and `/activity`. Verify: `npm run lint` and the typecheck pass
- [ ] 4.2 Add the About section at the end of the settings navigation (`UserSettingsModal.tsx`, `SettingsSection`): version and commit with `CopyableLine`, notes link, update state, Check now, the two toggles saving like other fields. Verify: component test or Playwright check for a release, a dev build and checks-off
- [ ] 4.3 Add the marker on the rail's settings button and the About entry while an update is available. Verify: shown when `latest` is set, gone when checks are turned off
- [ ] 4.4 Add Update: when `window.vistaApp` is present, start the download and show progress, then a confirm dialog listing `/activity` and the data-reset warning (exact or generic per `data_reset`); without the bridge show `install_command` with copy. Verify: Playwright against a stubbed backend for both paths and for cancel
- [ ] 4.5 Run `./scripts/ci-local.sh ui` and verify it passes (not in a checkout running `launch.sh`: it rebuilds `ui/.next`)

## 5. Electron: bridge and hand-off

- [ ] 5.1 Add `electron/src/main-preload.cjs` (`isVistaApp`, `restartToUpdate(version)`, `onUpdateEvent`) to the main window only, and its `ipcMain` handler checking sender, origin, the backend's `ready` state and a re-hash against `ready.json`. Verify: `npm test` cases for an off-origin sender, a child window, no ready download, a version mismatch and a hash mismatch, each refused
- [ ] 5.2 Add the helper scripts (`electron/update/apply-update.sh`, `apply-update.ps1`): wait for no process under the install folder (60 s cap), run the cached installer with `--version`, `--no-launch`, `VISTA_INSTALL_BASE_URL=file://…`, `VISTA_INSTALL_DIR`, then launch the new app or write `last-failure.json` and launch the old one; log to `VISTA_HOME/logs/update.log`. Verify: hermetic tests in `electron/test/apply-update.test.js` with a fake installer that succeeds, fails, and a fake process that never exits
- [ ] 5.3 Wire `restartToUpdate` to spawn the helper detached and call `requestQuit()`. Verify: unit test that the helper is spawned before stop and the session stops through the normal path
- [ ] 5.4 Add the `update_failed` state to `startup-protocol.js` and the startup window (reason, Open logs on `update.log`, Copy diagnostics, then continue starting); `main.js` reads and removes `last-failure.json` on start. Verify: `npm test` (every code has a message) and an e2e case in `npm run test:e2e` (needs a display; not in PR CI)
- [ ] 5.5 Ship the helpers in the package beside the window. Verify: `scripts/smoke_test_package.sh` checks they are present

## 6. Installers and release workflow

- [ ] 6.1 Call `Assert-NotRunning` again in `install.ps1` just before the swap. Verify: a case in `scripts/test_install.ps1`; `./scripts/ci-local.sh install`
- [ ] 6.2 Publish `vista-schema.sqlite` (an empty DB from `init_db`) with every release. Verify: `.github/scripts/draft-release.sh` attaches it, and a local dry run of `.github/scripts/` lists it
- [ ] 6.3 Add `.github/scripts/data-compat.sh`: walk earlier release tags newest first, use their `vista-schema.sqlite` (or build from the tag when absent), run the new `schema_check`, write `update.json` with `data_compatible_from`; fail the run on error; call it from `release.yml` before drafting. Verify: run it locally against v0.2.3/v0.2.4 tags and a fixture DB with a dropped column
- [ ] 6.4 Add `update.json` and `vista-schema.sqlite` to the draft checklist in `docs/releasing.md`. Verify: `./scripts/ci-local.sh docs` passes

## 7. Docs and end-to-end

- [ ] 7.1 Document checking, opting out and prereleases, Update and restart, and recovery with `--version` in `docs/installing.md`; note in the first release's notes that reaching it is a manual upgrade. Verify: `./scripts/ci-local.sh docs` passes
- [ ] 7.2 Manual end-to-end on macOS (validation lane, not PR CI): install 0.0.x from a fake release served by `VISTA_BACKEND_UPDATES__GITHUB_API` and a `file://` base, update to a newer fake, confirm the new version opens with chats kept; then make the installer fail and confirm the old version reopens with the failure state. Repeat on Linux and Windows. Record results in `docs/validation-lane.md`
