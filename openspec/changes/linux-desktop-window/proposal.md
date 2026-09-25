## Why

`electron-desktop-shell` gave the macOS package its own window, but Linux packages still end
by printing an address. The only reason is Chromium's sandbox. Ubuntu 23.10 and later
restrict unprivileged user namespaces to programs an AppArmor profile allows, and a window
unpacked from a tarball has no profile, so it aborts at start-up. The proper answer is
native packaging, where a `.deb` installs that profile. Packaging needs decisions still
open for every platform, such as signing on macOS and Windows, so it is a later change.
Until then, Linux users should get the window now, including on Ubuntu.

## What Changes

- `build_local_package.sh` stages the window on Linux as `app/window/VISTA`. It is built
  by the existing `electron/scripts/package.js --platform linux`, needs no signing, and is
  named by the manifest's `window.exe` exactly as on macOS.
- `./vista` opens the window on Linux when there is a graphical session (`DISPLAY` or
  `WAYLAND_DISPLAY`, not over SSH). **BREAKING (behaviour), Linux only:** the default no
  longer just prints an address, the same change macOS had; `./vista --browser` keeps
  today's behaviour. Before starting the window, the launcher decides whether
  Chromium's sandbox can run:
  - where the host allows it (Debian 13, Fedora, RHEL 10, or Ubuntu with VISTA's
    AppArmor profile installed), the window runs sandboxed, as today on macOS;
  - where the host blocks the user namespaces the sandbox needs, the window runs with
    `--no-sandbox`. That means Ubuntu's restriction with VISTA's profile not installed, or
    a host such as a container that blocks them some other way. The launcher checks by
    trying to create one, says so on every start and, on Ubuntu, names the one-time
    command that turns the sandbox back on. **Accepted risk**, recorded in the design.
- While the window runs without the sandbox, PDFs open in the system browser rather than
  a VISTA window, which keeps the largest parser of content VISTA does not produce inside
  the browser's own sandbox.
- A window that fails, or whose system libraries are missing, no longer takes VISTA
  down. The launcher reports it and carries on in browser mode. This also covers macOS,
  where today a window crash stops every service. Closing or quitting the window still
  stops VISTA.
- The package ships the AppArmor profile, and the README documents the one-time `sudo`
  step that installs it. A later `.deb` installs the same file.
- `./launch.sh --electron` applies the same sandbox decision in development.
- The Linux build container gains `xvfb` and Electron's runtime libraries, so the build's
  "the window loads the UI" check runs there. That check alone uses `--no-sandbox`,
  because the container runs as root.
- A new advisory CI job runs the window's Playwright tests on Linux under `xvfb`. Until
  now they ran only by hand on macOS.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `desktop-window` (added by `electron-desktop-shell`, not yet archived):
  - a new requirement that the renderer sandbox is on wherever the host permits it, and
    off only where the host's policy prevents it, with the researcher told each time;
  - "VISTA's own documents and files are usable without tabs": PDFs open in the system
    browser while the sandbox is off;
  - "Window mode where it cannot run": adds a window that fails at start-up and missing
    window libraries to the no-display case.

## Impact

- **Modified**: `scripts/package_launcher.sh`, `scripts/launch.sh`,
  `scripts/build_local_package.sh`, `scripts/smoke_test_package.sh`,
  `scripts/Dockerfile.build`, `electron/src/main.js` (PDF routing and a sandbox status
  line in `window.log`), `electron/test/`, `.gitlab-ci.yml`, `README.md`,
  `docs/validation-lane.md`, `AGENTS.md`.
- **New**: the AppArmor profile file in `electron/`, shipped as `app/window/`.
- **Package size**: Linux archives grow by about 100 MB compressed.
- **Unchanged**: the macOS window (apart from the start-up fallback), backend, MCP
  servers, the UI, and Linux's KVM and glibc 2.39 requirements.

## Non-goals

Native packages (`.app`, `.deb`/`.rpm`, a Windows installer), signing, Flatpak or Snap.
Running the launcher's `sudo` step for the user. A window over SSH X forwarding. Windows
(`windows-support`). Changing the sandbox on macOS.

Depends on `electron-desktop-shell`, whose branch this one is cut from.
