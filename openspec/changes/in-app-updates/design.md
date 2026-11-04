## Context

See proposal.md for motivation, and the specs for the behaviour. What exists today:

- **Version.** The tag is the version (`release-builds`). The build writes it to the package's
  `VERSION` file and `manifest.json`, the launchers export it as `VISTA_VERSION`, and the
  backend reads it as `Settings.version` (`backend/src/vista_backend/config.py`), `dev` on a
  checkout. Only `/openapi.json` shows it. The `0.1.0` in `ui/package.json`,
  `electron/package.json` and `backend/pyproject.toml` is never updated and must not be shown.
- **Settings.** Per-install settings are one `LocalSettingsTable` row
  (`db/schemas.py`), read and written via `GET`/`PUT /settings` (`api/settings.py`,
  `services/local_settings.py`), exposed through `LocalSettingsUpdate` / `LocalSettingsPublic`.
  Deployment switches live on the env `Settings`; `onboarding_tour` shows the split.
- **Window.** The main window has no preload (`electron/src/main.js` `webPreferences`). Only the
  startup window has a bridge (`startup-preload.cjs`, `window.vistaStartup`), and its handlers
  reject other senders (`fromStartupWindow`). Quit is `requestQuit()` →
  `LauncherController.stop()` (SIGTERM, or closing stdin on Windows; 12 s, then SIGKILL). There
  is no relaunch path.
- **Installers.** `install.sh` / `install.ps1` are release assets only, not in the package.
  Both take `--version` / `-Version`, `--no-launch` / `-NoLaunch`, and
  `VISTA_INSTALL_BASE_URL` (http(s) or `file://`). They check the archive's `.sha256`, swap
  into the fixed folder, never touch `VISTA_HOME`, and open the new VISTA. `install.sh` refuses
  while any process runs from the folder (`pgrep -f`) before download and again before the
  swap; `install.ps1` only before download.
- **Activity.** Agent runs are in `chat_run_registry` (`services/chat_run.py`, `active()`),
  debates in `_BACKGROUND` (`api/debate.py`), watched jobs via
  `campaign_service.list_open_jobs`. No endpoint combines them.

## Goals / Non-Goals

**Goals:**
- Network, version and verification logic in Python, tested hermetically with pytest.
- Electron's new surface kept to one narrow bridge and one helper, with no logic that decides
  what to trust.
- Reuse the release installers unchanged in behaviour, so an update and a manual upgrade take
  the same path.

**Non-Goals:**
- Changing how packages are built or laid out.
- Verifying anything beyond GitHub's per-asset sha256 digest (decided: digest is the trust
  root, permanently).

## Decisions

### D1. Backend owns check, download and verification

`services/update_check.py`:
`check(current, settings, fetch) -> UpdateStatus` and `services/update_download.py`
(streaming download, progress, verification). Routes:

- `GET /version` → `{version, commit, checks: "on" | "off" | "dev_build", latest?,
  notes_url?, checked_at?, data_reset?}`
- `POST /updates/check` — check now.
- `POST /updates/download` — start the download of the release `GET /version` offered;
  `GET /updates/download` — `{state: idle|downloading|ready|failed, bytes, total, error?}`.
- `GET /activity` — `{chat_runs: [...], debates: n, watched_jobs: [...]}`.

Stable: `GET {api_base}/repos/Genesis-VISTA/vista/releases/latest`. Prereleases: `GET
/releases?per_page=20`, filter out drafts, take the highest by semver. `api_base` is a
`Settings` field (`VISTA_BACKEND_UPDATES__GITHUB_API`, default `https://api.github.com`) so
tests and e2e serve a fake. Version comparison uses `packaging.version`. It accepts `0.2.2rc1`
style and orders `rc < final`. Tags are normalised (`v0.2.2-rc1` → `0.2.2rc1`). Any build
metadata or `dev`/`-dirty` marks the build as a dev build (no check). The scheduler is an
asyncio task in the lifespan, next to the campaign monitor: run at start, then sleep until 24h
after the last success (retry hourly after a failure). The result is cached in memory and
`checked_at` is stored on the settings row so it survives a restart. Requests send a
`User-Agent: VISTA/<version>` header and nothing identifying.

*Alternatives:* checking in Electron (no `--no-electron` dev path, Node tests, duplicate settings);
checking from the UI (one request per tab, nowhere to cache). Rejected in grilling.

### D2. Settings

Three additive columns on `LocalSettingsTable`, all with server defaults:
- `update_checks` (bool, default true)
- `update_prereleases` (bool, default false)
- `update_checked_at` (nullable timestamp)

The first two join `LocalSettingsUpdate`/`LocalSettingsPublic`. `schema_check` accepts all of
them (additive), so no reset.

### D3. Verification and the cache

Cache: `VISTA_HOME/updates/<version>/`. Required assets:
- `vista-<version>-<platform>.<ext>`
- its `.sha256`
- `install.sh` or `install.ps1`
- `update.json`

They are found by name in the release's `assets[]`. Each asset's API `digest`
(`sha256:<hex>`) is checked after a streamed download to `*.part`, then renamed into place.
The archive is also checked against its `.sha256`. If an asset lacks a `digest`, the update is
refused rather than trusted. Before downloading, check free space against the asset size plus
the installer's 7 GB floor. When done, write `ready.json` with the version and each file's
hash. The cache is removed by the next backend start whose version equals the cached one
(success), or replaced by a newer download.

`update.json` is fetched during the check, not only the download, since the confirm dialog
needs `data_reset` before anything is downloaded. `data_reset` is true when the installed
version is below `data_compatible_from`. A release without `update.json` (all releases before
this change) yields `data_reset: null`, and the dialog falls back to the generic sentence.

### D4. The bridge: `window.vistaApp`

Add `main-preload.cjs` to the main window only, exposing:
- `isVistaApp: true`
- `restartToUpdate(version): Promise<{ok, reason?}>`
- `onUpdateEvent(cb)`, for "stopping" events only

The handler in `main.js` checks:
1. The sender is the main window's webContents.
2. The frame URL's origin is VISTA's origin (`routing.js`).
3. The backend's `GET /updates/download` says `ready` for that version.
4. Electron re-hashes the cached installer and archive against `ready.json`.

Paths are never taken from the page. The page only passes a version string, which must equal
the ready one. The UI detects the bridge by `window.vistaApp?.isVistaApp`. VISTA runs only in
its window, so there is no browser path: without the bridge (only `./launch.sh --no-electron`,
a dev build that is never offered an update), Restart now says it needs the window.

*Alternatives:* relaying through the backend or the launcher protocol, or a `vista-update://`
URL caught by `routing.js`. Both put a control path somewhere it doesn't belong.

### D5. Hand-off helper

Electron writes a small helper script into the cache:
- `apply-update.sh` on macOS and Linux
- `apply-update.ps1` on Windows

It spawns the helper detached, with stdio going to `VISTA_HOME/logs/update.log`, and passes:
- the PID of Electron and the launcher
- the install folder (from `resolvePackage`)
- the version
- the cache path

Then it calls `requestQuit()`. The helper:

1. Waits until no process runs from the install folder, using the same test as the
   installer's running check (`pgrep -f "$dir/"` / `Get-Process` path prefix). Gives up after
   60 s.
2. Runs the cached installer with `--version X --no-launch`,
   `VISTA_INSTALL_BASE_URL=file://<cache>`, and `VISTA_INSTALL_DIR=<install folder>`.
3. Success: launches the new app with the same command the installer's launch step uses. The
   helper launches it, not the installer, so the failure path below has one owner.
4. Failure: writes `VISTA_HOME/updates/last-failure.json`
   `{version, reason, log}` and launches the old app (still in place) with the same command.

The helper runs from the cache, not the install folder, so the swap doesn't trip over it. Its
inputs travel in the environment (`VISTA_UPDATE_*`), never its command line, so the wait's
`pgrep -f "$dir/"` can't match the helper itself. The helpers ship as plain files in the
window's `resources/` (`extraResource`, not inside `app.asar`, which no shell can read), and
Electron copies the platform's one into the update's folder before starting it. On Windows it
runs as `powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File`, spawned detached and
hidden, so no console window flashes.

The helper also starts the update status window (D9) before it waits, and writes each step to
the status file it shows.

### D6. Failure state in the startup window

On start, `main.js` reads and removes `last-failure.json`, and sends the startup window an
`update` notice (`updateFailureNotice` in `startup-protocol.js`: "Update to 0.2.5 failed" and
the reason). The startup window shows it above the normal progress, with Open logs (the logs
archive, `update.log` included) and Copy diagnostics (which then name the failed update), and
continues starting. Because the startup window normally closes the moment VISTA is ready, it
holds the main window while the notice shows and offers "Continue to VISTA" once ready, so the
notice is read rather than flashed.

### D7. Data-compatibility step in the release workflow

`.github/scripts/data-compat.sh`, run once on Linux as the workflow's `data-compat` job,
alongside the package jobs (so a refusal costs no build time); `release` needs it, and
`draft-release.sh` refuses a folder without its two files:

1. Builds an empty DB with the new code (`init_db`).
2. For each earlier release tag, newest first, takes that release's empty DB and runs the new
   `schema_check` against it, stopping at the first refusal.
3. Writes `update.json`.

To avoid rebuilding old trees every release, each release also publishes its own empty DB as
`vista-schema.sqlite`. The step downloads earlier releases' copies, and builds one from the
tag (`git worktree` + `uv run`) only for releases published before this change. A failure
fails the run, so no draft is created. The ordering of tags, the check (the startup one,
`schema_problems(assume_additive=True)`) and the verdict live in
`vista_backend/db/release_schema.py`, so they are unit-tested with the backend; the script
only fetches and builds. When the newest earlier release is refused, `data_compatible_from`
is the new version itself.

### D8. `install.ps1` second running check

`install.ps1` calls `Assert-NotRunning` again just before the swap, matching `install.sh`. The
helper's wait makes the race unlikely, but the installer is also run by hand. It turned out to
do so already; `test_install.ps1` now checks that the second call sits between unpacking and
the swap, since the running-process case stops at the first.

### D9. The update status window

Measured on macOS, the installer runs for 1 min 39 s after VISTA closes, with nothing on screen
until the new version's startup window. A notification fades, so a window stays up instead,
in the startup window's style: "Installing VISTA 0.2.6" and one status line.

Nothing on screen can run from the install folder, which the installer replaces and the helper
waits on. A second app would carry its own Electron runtime, the bulk of the window's size, so
the status window is VISTA's own window run from a copy:

1. **The copy.** In the restart handler, before the hand-off, Electron copies its own app into
   the update's folder as `window/`: `VISTA.app` on macOS (`cp -c`, a clone, so instant and no
   extra disk), `app/window/` on Linux and `app\window\` on Windows (a real copy). A failed
   copy is not fatal: the update goes ahead without the status window, and the log says so.
   The backend's `clean_cache` already removes the folder afterwards.
2. **A mode of `main.js`, `--update-status <folder>`.** Taken before anything else: no
   `resolvePackage` (a copy has no package around it, and the check must not be loosened for
   it), no single-instance lock and its own `userData` (`<folder>/window-data`), since it has
   the same identity as the VISTA about to start and would otherwise be taken for it. No
   launcher, no backend. One small window (`update.html`, the startup window's CSS) polls
   `<folder>/status.json` every 250 ms.
3. **The helper drives it.** `apply-update.sh` / `.ps1` start the copy first, its binary
   directly (not `open`, so macOS registers no second VISTA; Linux takes its flags from the
   copy's own `window-sandbox`, which, with the copy outside the AppArmor profile's path, may
   say `--no-sandbox`: acceptable for a window that loads one local file). Then they write
   `status.json` `{version, step, detail}` at each step: `waiting`, `installing` with the
   installer's own `==>` lines as `detail`, `opening`, or `failed` with the reason.
4. **Closing.** Before starting the status window, the helper writes a marker,
   `VISTA_HOME/updates/status-window.json`. On start, `main.js` (any version with this change:
   the new one, or the old one after a failure) removes it once its startup window is showing,
   and the status window, seeing it gone, exits. The helper writes it, not the status window:
   after a failure VISTA reopens within a second, and on macOS its startup window showed before
   the status window had started, so a marker the window wrote itself came too late and was
   never removed. A file, not a PID, so a reused PID can never stop the wrong process. The status window also exits by itself two minutes after
   `status.json` last says `opening` or `failed`, so a reopen that never comes does not leave
   it up. On a failure it does not repeat the reason at length: the reopened version's startup
   window owns the failure notice (D6).

The helper's running check matches the install folder's path, and the copy is under
`VISTA_HOME/updates`, so it is never counted as VISTA still running.

## Risks / Trade-offs

- **The helper's wait times out because a sandbox or service outlives the launcher's stop.**
  The installer refuses, the helper takes the failure path, and the old VISTA reopens and says
  so. Nothing is lost.
- **The GitHub API rate limit (60/h per IP) is shared behind a lab NAT.** One check a day per
  install. A `403`/`429` is logged and treated as no update.
- **A compromised release (repo write access) is installed.** Accepted: the same trust as
  the documented `curl | bash`. Decided in grilling.
- **Old installers or archives lack a `digest`.** GitHub reports digests for assets uploaded
  since mid-2025. An asset without one is refused, never assumed good.
- **Researchers on a release from before this change.** They only get the notice once they
  are on a release that includes it, so the first hop is manual. The release notes should
  say so.
- **macOS Gatekeeper.** Files fetched by the backend carry no quarantine attribute, so the
  ad-hoc-signed app opens as it does after `curl | bash`. Verify on real hardware in the
  release smoke test.
- **Windows locks a file being replaced.** The helper waits for every process under the
  folder, the installer re-checks (D8), and a refusal takes the failure path.
- **Prerelease channel.** A researcher who opts in, then opts out while on an RC, is offered
  the next stable once it is newer. They are never offered a downgrade.

## Migration Plan

- Additive settings columns: no reset.
- Ship in one release. Releases before it have no `update.json` or `vista-schema.sqlite`,
  which D3 and D7 handle.
- Rollback: turn checks off per install, or revert the change. The release-workflow step can
  be removed on its own.

## Open Questions

- Whether `GET /activity` should also count running sandboxes. They stop with the agent run,
  so they're likely covered by `chat_runs`.
