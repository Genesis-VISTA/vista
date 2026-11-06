## Why

A researcher cannot see which VISTA they are running without reading `/openapi.json`, and
nothing tells them a newer release exists: upgrading means knowing to re-run the one-line
installer. Releases now ship regularly from `Genesis-VISTA/vista`, so installs drift behind
silently and bug reports arrive without a version.

## What Changes

- Settings gains an **About** section: the installed version and commit (copyable), a link to
  the release notes, and the update state. A backend `GET /version` serves it from
  `settings.version`.
- The backend **checks GitHub for a newer release** on startup and every 24 hours, and on
  "Check now". On by default, off with a local setting; a second local setting opts into
  prereleases. Development and local builds (`dev`, `+N.gSHA`, `-dirty`) never check. A failed
  check is silent.
- A newer release puts a **dot on the Settings entry**; there is no banner, toast or skip
  state.
- **Update and restart**: on click, the backend downloads the release's archive, `.sha256` and
  installer into a cache under `VISTA_HOME`, verifying each against the sha256 digest the
  GitHub API reports. A confirm dialog lists running work (agent runs, debates, watched HPC
  jobs) and warns when the update will ask to reset data. Electron then quits normally and a
  detached helper runs the cached installer with `--version X` from the cache; the installer
  opens the new VISTA. If it fails, the old VISTA reopens and its startup window says why,
  with Open logs and Copy diagnostics.
- The main window gets a **narrow preload bridge** (`window.vistaApp`) for update state and
  the restart hand-off, accepted only from VISTA's own origin in the main window.
- The **release workflow publishes `update.json`**, recording `data_compatible_from`: the
  oldest earlier release whose database the new code's `schema_check` accepts.
- `install.ps1` re-checks that VISTA is not running just before the swap, as `install.sh`
  does.

## Capabilities

### New Capabilities

- `app-updates`: version reporting, the update check and its settings, the verified download,
  and the update-and-restart hand-off with its failure path.

### Modified Capabilities

- `settings-modal`: adds the About section and the Settings update dot.
- `desktop-window`: the main window's page may use one narrow update bridge; everything else
  stays browser-tab only.
- `release-builds`: releases carry `update.json` with their data compatibility.

Related: `local-data-compatibility` (the reset prompt warned about) and the `release-builds`
installers, reused as they are.

## Non-goals

- No in-app rollback or downgrade; recovery stays `install.sh --version <old>`.
- No build provenance attestations or signatures beyond GitHub's asset digest.
- No electron-updater / Squirrel, delta updates, background pre-download or silent installs.
- No "skip this version" or snooze.
- No VISTAGuard work.

## Impact

- Backend: update check and download services, `GET /version`, `/updates/*`, `GET /activity`;
  additive `LocalSettingsTable` columns.
- UI: About section, the Settings marker, the confirm dialog.
- Electron: main-window preload, update helper (bash, PowerShell), `update_failed` state.
- Release: data-compatibility step, `update.json` and `vista-schema.sqlite` assets.
- Installers: `install.ps1` second running check.
- Network: one `api.github.com` request per check, by default.
