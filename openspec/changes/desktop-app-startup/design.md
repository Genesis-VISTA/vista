## Context

This change began as `mac-native-startup` (Feiyi Wang, #70). That change built a macOS startup
experience: the application starts first, runs the package launcher as its child, shows real
progress, and owns the session's lifetime. It was macOS-only, and it made Developer ID signing and
notarization a release gate. This revision keeps that design, widens it to Linux and Windows, and
removes the signing gate. Decisions D1–D8 below keep Feiyi's numbering; each says where it
changed.

Today, on every platform, the package launcher owns everything. `package_launcher.sh` on macOS
and Linux, and `vista.ps1` behind `vista.cmd` on Windows, check the host and ports, install
missing bundled resources, import the sandbox image, start three services, wait for their health
endpoints, and only then start the Electron window. The window knows nothing about the services
and exits when it is closed. Cleanup is reliable, but a terminal (macOS and Linux) or console
(Windows) is visible throughout, and it is the session's real owner: closing it stops VISTA.

Since `mac-native-startup` branched, `github-release-builds` shipped. It builds the packages on
GitHub-hosted runners (macOS on `macos-15`) and publishes `install.sh` and `install.ps1`
one-line installers. Because those installers download with `curl` or `Invoke-WebRequest`, the
macOS package is never quarantined, so an ad-hoc-signed app launches through LaunchServices
without a Gatekeeper prompt. This was tested on 2026-09-22. That is what makes an app-first
macOS package possible without a Developer ID.

## Goals / Non-Goals

**Goals:**

- Launch VISTA from Finder, Spotlight or the Dock, from the Linux app menu, and from the Windows
  Start menu, with no terminal or console.
- Show real preflight, first-run and service readiness activity immediately, in one startup
  window shared by every platform.
- Present expected failures with a remedy, logs, and Retry and Quit, in the application.
- Keep one owner for service startup and cleanup on each platform: the existing launcher.
- Keep the main VISTA renderer at its current browser-equivalent privilege level.
- Install into one fixed folder per platform, so app-menu entries and pins survive upgrades.

**Non-Goals:**

- Reimplementing launcher checks or service supervision in JavaScript.
- Putting startup inside Next.js, which is not available until the last startup phase.
- Developer ID signing or notarization. `signing-spike.md` records what a later signing lane
  would have to handle.
- A layered Icon Composer icon. The packaged `.icns` renders at the same 824/1024 size as other
  installed apps (measured through NSWorkspace on macOS 26.7). The layered icon follows only if a
  live Dock check shows Tahoe placing it in its smaller legacy slot.
- A single self-contained app bundle, auto-update, a menu-bar or tray mode, app stores, and
  VISTAGuard.

## Decisions

### D1. Every platform is app-first

*Changed from macOS-only.* Each package's documented entry point is the application:

- macOS: `VISTA.app` at the package root.
- Linux: the app-menu entry, which runs the pre-window launcher (D9).
- Windows: the Start-menu entry, which opens `VISTA.exe` (D10).

The terminal launchers (`vista`, `vista.cmd`) stay as diagnostic commands and keep their current
behaviour when run by hand: preflight, setup, services, then the window, in a terminal. The
smoke test, SSH sessions and support use them. `./launch.sh` is unchanged; it passes `--url` and
never enters startup mode.

### D2. The launcher stays the service supervisor

*Kept, widened.* Electron spawns the launcher with `--supervised --progress=jsonl`. That mode does
the existing work, skips the window checks and the window, prints no interactive instructions,
and stays alive after readiness. On quit, Electron asks it to stop and its existing cleanup stops
every service and sandbox.

The launcher reports protocol v1 on stdout, one JSON object per line. Its human-readable output
moves to stderr, which the application records in `window.log`, and the services keep their own
log files. `phase` is one of `preflight`, `resources`, `sandbox`, `mcp`, `backend`, `ui` or
`stopping`. `state` is one of `pending`, `running`, `complete`, `ready` or `failed`. Logic keys
only on `protocol`, `phase`, `state` and `code`. Work skipped because it was already done
reports `complete` with `skipped: true`. An unsupported protocol version is a fatal error, not a
best-effort parse.

```json
{"protocol":1,"phase":"preflight","state":"running","label":"Checking this computer"}
{"protocol":1,"phase":"ui","state":"ready","url":"http://127.0.0.1:3000"}
{"protocol":1,"phase":"backend","state":"failed","code":"health-timeout","log":"backend.log"}
```

The bash mode exists on `mac-native-startup` and is restricted to macOS. This change lifts that
restriction for Linux and adds the same mode to `vista.ps1` (D10). The protocol, its error codes
and the failure messages stay in one place, `electron/src/startup-protocol.js`, and both
launchers emit the same events.

### D3. Startup and the main UI use separate windows

*Kept.* Electron first opens a local startup window. Its preload exposes only progress
subscriptions plus `retry`, `openLogs`, `copyDiagnostics` and `quit`. The renderer is
context-isolated and sandboxed, with no Node and no generic IPC. On `ui`/`ready`, Electron
creates the existing main window hidden, at its 1280 × 860 default, loads the URL, shows it on
`ready-to-show`, and then closes the startup window. The main window keeps its existing
`webPreferences` and has no preload.

### D4. Activity is truthful, and the window is one design

*Kept, widened.* The startup window shows an ordered activity list and one current activity, with
no percentages or time estimates. Expected failures stay in the window with a concise message,
the failed activity, and Open Logs, Copy Diagnostics, Retry and Quit. Copy Diagnostics includes
versions, phase, error code and log paths, never environment values or credentials. Retry starts
a fresh launcher only after the previous one has fully exited.

The page is the same on every platform, inside that platform's native window frame. It shows the
VISTA icon and follows the system light/dark setting through the UI's palette. Mac-specific copy
becomes platform-neutral: "Checking this Mac" becomes "Checking this computer".

### D5. The app owns the visible lifecycle

*Kept.* The single-instance lock is taken before the launcher starts, so a second launch focuses
the startup or main window and starts no second stack. Closing either window, Cmd-Q, or the
application's Quit sends the launcher a stop request, shows a stopping state if cleanup takes a
moment, and waits up to the launcher's grace period before forcing it. VISTA never keeps running
without a window. If the application dies outright, the launcher sees end-of-file on its stdin,
which the application held, and runs its normal cleanup.

### D6. One fixed install folder per platform

*Changed: the package layout is per platform, and installs move to fixed folders.* The package
stays a relocatable folder. The application finds its package root relative to itself, and
refuses with a graphical explanation if it has been separated from it:

- macOS: `VISTA.app` at the package root, beside `vista`, `app/` and `manifest.json`.
- Linux and Windows: the window stays at `app/window/`, and the package root is two levels up.

The manifest records `entrypoint` and `diagnostic_launcher` for each platform.

The installers stop naming the folder after the version:

| Platform | Install folder | Was |
|---|---|---|
| macOS | `/Applications/VISTA/`, else `~/Applications/VISTA/` | `~/.local/share/vista/vista-<ver>-mac-arm64/` |
| Linux | `~/.local/share/vista/app/` | `~/.local/share/vista/vista-<ver>-linux-x86/` |
| Windows | `%LOCALAPPDATA%\VISTA\app\` | unchanged |

A fixed folder means Dock pins, the Linux desktop entry and the Start-menu shortcut never point at
a deleted version. State stays in `VISTA_HOME`, `~/.vista` by default, per user.

On macOS the installer prefers `/Applications/VISTA/` and falls back to `~/Applications/VISTA/`.
A spike on macOS 26 (2026-10-06) measured the difference:

| Where | Apps view | Spotlight | Finder's Applications |
|---|---|---|---|
| `/Applications/VISTA/VISTA.app` | yes | yes | yes, in a `VISTA` folder |
| `~/Applications/VISTA/VISTA.app` | yes | yes | no: `~/Applications` is not in the sidebar |
| a symlink or alias in `/Applications` | no | no | yes |

So the system folder is the only one that also shows in Finder's Applications, which is where a
researcher who does not search will look. A symlink or alias there is not used: it shows in Finder
only. The names shown are the bundle's file name, so the app stays `VISTA.app`.

`/Applications` is `root:admin` 775, so an administrator writes there without `sudo` and a
standard user cannot. The installer decides by what it can write, not by group membership: the
existing `/Applications/VISTA` when there is one, since another administrator's install is theirs
(`755`) and cannot be upgraded by anyone else, otherwise `/Applications` itself. When neither is
writable it installs into `~/Applications/VISTA/` and says why. After installing it registers the
app with LaunchServices (`lsregister -f`) and Spotlight (`mdimport`): left alone, indexing took
half a minute or more, longer after a rename.

### D7. Distribution without a signing gate

*Reversed.* Feiyi's D7 made Developer ID signing, notarization and a Gatekeeper assessment a
release gate, and rejected any "bypass" path. This change ships the macOS package ad-hoc signed.
That is acceptable because the supported route never quarantines it:

- **Installer:** `curl … | bash` downloads with curl, which sets no quarantine, and installs into
  `/Applications/VISTA/` or `~/Applications/VISTA/` (D6).
- **By hand:** the release notes' manual steps already say to download with `curl`, not a
  browser. They end by moving the folder into `/Applications/VISTA/` (or `~/Applications/VISTA/`)
  and opening `VISTA.app`.
  The existing `xattr -dr com.apple.quarantine` and System Settings fallback stays for a
  browser download.

The build keeps signing only the window bundle, ad hoc, and never re-signs `msb`, preserving its
hypervisor entitlement. A later signing lane can replace this without changing the layout.
Windows stays unsigned, as today.

macOS's App Management protection ("Terminal was prevented from modifying apps") did not interfere
with upgrading or removing the ad-hoc-signed app from Terminal, in either install folder (spike,
2026-10-06). That is probably because it has no Team ID to protect. A Developer-ID-signed build
could start triggering it on upgrade, so a signing lane has to retest the installer's upgrade.

### D8. Development and test modes stay explicit

*Kept.* `./launch.sh logs` keeps its behaviour: it passes `--dev --url=…`, and startup mode is
entered only through `--startup`, `--launcher=` or a macOS launch with no URL. `VISTA Dev.app`
(macOS only) packages just Electron from the checkout. It runs `./launch.sh logs --no-build
--no-electron` behind the same startup window, with its own state folder, `~/.vista-dev`. The
Electron tests use a fake launcher for every startup path.

### D9. Linux: a pre-window launcher and a desktop entry

*New.* Chromium's renderer sandbox is fixed when the process starts. Today `package_launcher.sh`
runs `electron/linux/window-sandbox`, and if the window dies in its first seconds it retries once
without the sandbox. App-first, Electron starts before the launcher exists, so those decisions
move into a small script in front of Electron, `app/window/vista-app`. It:

1. checks for a display and refuses as root, as `can_show_window` does today;
2. checks the window's system libraries (the `ldd` check);
3. runs `window-sandbox` and starts Electron with `--startup`, adding `--no-sandbox` only when
   told to;
4. if Electron exits in its first seconds with any status other than 75 (another VISTA
   window is already open), records the first attempt's log and starts it once more without the
   sandbox, as today.

Failures in steps 1–2, the only ones that leave no window to report in, are shown with
`notify-send` when it exists, and always written to `~/.vista/logs/window.log`.

The installer writes `~/.local/share/applications/vista.desktop` (`Terminal=false`,
`Exec=<install>/app/window/vista-app`, `Icon=vista`, `StartupWMClass=vista`). It installs
`icon.png` as `~/.local/share/icons/hicolor/512x512/apps/vista.png`. Electron's
`package.json` gains `"desktopName": "vista.desktop"`, so Wayland compositors match the running
window to the entry by app id. Under X11 the window's WM_CLASS is `vista` (measured under Xvfb,
2026-10-06), not the product name, so that is the `StartupWMClass`.

### D10. Windows: a supervised `vista.ps1` and a console-free entry

*New.* `vista.ps1` gains `-Supervised -Progress jsonl` with the same events and codes as the bash
mode. Two Windows details change how it is driven:

- **Stopping.** Electron's `child.kill()` is a `TerminateProcess` on Windows, which no script can
  trap. So closing the launcher's stdin is the polite stop request there: `vista.ps1` watches for
  end-of-file and runs its normal stop. The kill-on-close job object `vista.ps1` already creates
  remains the backstop for a hard kill.
- **What `vista.cmd` did.** The application starts `powershell.exe -NoProfile -ExecutionPolicy
  Bypass -File vista.ps1 …` itself, hidden (`windowsHide`). Before that, it unblocks `vista.ps1`
  (removing Windows' downloaded-file mark) and checks for an `AllSigned` execution policy. If one
  is set, it shows `vista.cmd`'s existing message in the startup window instead of failing
  silently.

`install.ps1`'s Start-menu shortcut targets `app\window\VISTA.exe --startup`, and its icon is
already that executable's. The package root is found relative to `VISTA.exe`.

### D11. Installers: refuse while running, clean up the old layout

*New.* With fixed folders, an upgrade would replace files a running VISTA is using: it can crash
on macOS and Linux, and the files are locked on Windows. Both installers therefore first check
whether VISTA is running, by whether the UI port answers as VISTA or a VISTA window process
exists. If it is, they stop with "Close VISTA, then run this again" and change nothing.

After a successful install, they delete the previous layout's versioned folders, so no stale
4 GB copy is left. On macOS that includes the researcher's own `~/Applications/VISTA/` once VISTA
is installed in `/Applications/VISTA/` instead. They never delete a `/Applications/VISTA/` they
did not install into, which can belong to another account on the same Mac. Finder can write a
`.DS_Store` into a folder while it is open and make one removal fail with "Directory not empty",
so a removal is retried once, and what is still left is reported rather than failing an install
that has already succeeded. There is no other migration.

When the installer is asked to start VISTA, it opens the application rather than exec'ing the
terminal launcher: `open` on macOS, the desktop entry's command on Linux, and `VISTA.exe` on
Windows.

### D12. What the release build checks

*Changed.* Feiyi's macOS-only Finder-launch smoke step and the signing assertions are dropped. On
every platform, `smoke_test_package.sh` also runs the launcher in supervised mode with no window.
It asserts that the protocol events run in order to `ui`/`ready` with the right URL, that a
deliberate port conflict yields `preflight`/`failed`/`port-conflict`, and that a stop request
leaves no process or port. The existing service-health, retrieval and relocation checks are
unchanged. The macOS runner runs exactly what the others run.

Manual checks before a release cover only macOS: Dock icon and size, the Finder and Spotlight
launch, the curl install, quitting, and an immediate restart. The Linux desktop entry and Wayland
window icon, and the Windows Start-menu entry, are not checked by hand; that is an accepted risk.

## Risks / Trade-offs

- **The Linux app-menu icon under Wayland is unverified.** Matching depends on the app id and
  `StartupWMClass`, which cannot be checked without a GNOME session. Mitigation: values follow
  Electron's documented behaviour; the first user report is the check.
- **The Windows supervised mode is new code with no manual validation lane.** Mitigation: it
  emits the same protocol as bash, and the release smoke test exercises it on the Windows runner.
- **An unsigned macOS app opened from a browser download is blocked.** Mitigation: the documented
  routes avoid quarantine, and the `xattr` fallback stays in the release notes.
- **The app can be separated from its runtime.** It detects this and explains that the whole
  folder is the application.
- **Two windows can flicker at hand-off.** The main window loads hidden and the startup window
  closes only once the page is ready.
- **The branch stacks on `desktop-icon` (!145).** It needs another rebase once !145 is
  squash-merged.

## Migration Plan

State needs no migration. Installers delete old-layout package folders after installing (D11).
Rolling back means installing an older release by hand; its terminal launcher still works.

## Open Questions

- Whether ORNL can provide a Developer ID. If so, a later change adds signing without changing
  this layout, and retests the macOS upgrade against App Management (D7). To be discussed with
  Feiyi.
