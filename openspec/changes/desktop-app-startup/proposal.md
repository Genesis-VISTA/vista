## Why

Each package starts through a terminal launcher (`vista`, or `vista.cmd` in a console on
Windows). The terminal shows startup logs, the window appears only once the services are up,
closing the terminal stops VISTA, and expected failures such as an occupied port land in the
terminal rather than in the application.

VISTA should open like any desktop application, from the Dock, the app menu or the Start menu,
and show honest startup activity in its own window straight away.

This change takes over `mac-native-startup` (Feiyi Wang, #70), which built that experience for
macOS, and widens it. It now covers all three platforms, and it drops Developer ID signing as a
release gate: the one-line installer from `github-release-builds` downloads with `curl`, which
macOS does not quarantine. Signing stays possible later; `signing-spike.md` keeps its record.

## What Changes

- The application is the entry point on every platform. It opens a startup window, runs the
  existing package launcher as its child in a supervised mode, and owns the session's lifetime.
  The `vista` and `vista.cmd` launchers stay as diagnostic commands.
- The launcher's supervised mode reports progress over a versioned JSON-lines protocol: on
  macOS and Linux from `package_launcher.sh`, and on Windows from a new mode in `vista.ps1`.
- One startup window everywhere: real phases, actionable failures (Open Logs, Copy
  Diagnostics, Retry, Quit), the VISTA icon and the system appearance.
- Linux: a small pre-window launcher decides the window's renderer sandbox before Electron
  starts and reports failures that leave no window with a desktop notification. A desktop entry
  and icon put VISTA in the app menu.
- Windows: the Start-menu entry opens `VISTA.exe` with no console.
- Installers put each platform's package in a fixed folder (`~/Applications/VISTA`,
  `~/.local/share/vista/app`, `%LOCALAPPDATA%\VISTA\app`). They refuse to upgrade while VISTA
  is running and remove the previous layout's folders after the new install is in place.
- The release notes keep both install routes; the manual macOS steps end by opening `VISTA.app`.
- `VISTA Dev.app` stays as a macOS developer tool.

## Capabilities

### New Capabilities

- (none)

### Modified Capabilities

- `desktop-window`: the application opens a startup window first and owns startup on every
  platform; failures that leave no window; desktop integration.
- `laptop-distribution`: the application, not a terminal command, is each package's entry
  point; build verification checks the supervised protocol; no signing gate.
- `release-builds` (from `github-release-builds`, which must be archived first): fixed install
  folders, app-menu entries, upgrades while running, and manual steps that end in the app.

## Impact

`electron/`; the package launchers (`.sh`, `.ps1`) and a Linux pre-window launcher; the build,
smoke test and both installers; `.github/release-notes.md`, `README.md` and
`docs/validation-lane.md`.

## Non-goals

- Developer ID signing and notarization, a layered Icon Composer icon (only if a live Dock
  check shows the `.icns` in Tahoe's smaller legacy slot), auto-update and app stores.
- Moving the startup interface into Next.js, or changing the main VISTA UI.
- Running services after the last window closes; menu-bar or tray modes.
- A single self-contained app bundle. VISTAGuard.
