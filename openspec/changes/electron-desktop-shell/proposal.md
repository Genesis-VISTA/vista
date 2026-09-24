## Why

The prebuilt package (`prebuilt-laptop-package`) ends by printing `http://localhost:3000`
and leaving the researcher to open a browser tab. The tab is easy to lose among others,
and closing it leaves the services running. Closing the terminal, meanwhile, kills
them. VISTA should open as its own window, and closing that window should stop VISTA.

A spike on 2026-09-22 showed that an ad-hoc-signed Electron 44 bundle inside the tarball
launches without a Gatekeeper prompt once `./vista` has stripped quarantine. It adds about
121 MB compressed to a 2.0 GB archive. No Apple Developer ID is needed as long as the
terminal launcher stays the entry point.

## What Changes

- Add an `electron/` shell. It is a window only: it loads the UI address it is given and
  never starts or supervises services. It routes links: same-origin pop-ups open in-app,
  external links open in the system browser (including the Globus authorize link), and
  off-origin navigation is blocked. It adds a standard app menu (so Cmd-C/V work), runs
  as a single instance, and quits when its window closes.
- `./vista` (the package launcher) opens that window by default once services are
  healthy, and stops every service when the window closes. **BREAKING (behaviour):** the
  default no longer just prints an address; `./vista --browser` keeps today's behaviour,
  and the build's smoke test uses it.
- The package launcher stops services by process group and also traps `HUP`, so closing
  the window, pressing Ctrl-C, or closing the terminal leaves no sandbox or MCP process
  holding a port.
- `./launch.sh --electron` opens the dev server (`next dev`, hot reload) in the same
  shell, so Electron-only behaviour is exercised before it ships.
- `build_local_package.sh` stages the shell on macOS as a plain folder `app/window/`. It
  is rebranded and ad-hoc re-signed on its own, and `msb` is left untouched. The shell's
  version and size go into the manifest, and the build smoke-tests that it loads a page.

## Capabilities

### New Capabilities

- `desktop-window`: VISTA's interface runs in a dedicated application window whose
  lifetime is VISTA's lifetime, with link, download and navigation behaviour defined
  for a window that has no tabs or address bar.

### Modified Capabilities

- `laptop-distribution`: "Single command to install and run" now ends by opening the
  window, not by reporting an address, and gains a browser-mode opt-out.

## Impact

- **New**: `electron/`.
- **Modified**: `scripts/package_launcher.sh`, `launch.sh`, `build.sh`,
  `build_local_package.sh`, `smoke_test_package.sh`, `README.md`, `.gitlab-ci.yml`,
  and two "Download" links in the UI.
- **Unchanged**: backend, MCP servers, the browser experience, and Linux packages.

## Non-goals

Double-click launch, a `.app` in `/Applications`, and a `curl | bash` installer. Apple
Developer ID signing and notarization. Linux Electron (Ubuntu 24.04 AppArmor blocks
Chromium's sandbox) and Windows (see `windows-support`). Auto-update. Electron starting
or supervising services. A first-run progress window, since the terminal already shows
progress. Free-port fallback, because a changing port changes the origin and drops
`localStorage`. Authentication of the local API (a pre-existing gap, tracked
separately). Shrinking the download. VISTAGuard.

Related: `openspec/specs/laptop-distribution`, `openspec/changes/prebuilt-laptop-package`,
`openspec/changes/windows-support` (on its own branch).
