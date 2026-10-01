## Context

See `proposal.md` — Why. Decision identifiers (W, L, B, T) are for review comments.

Current state and constraints that shape the approach:

- **`./vista` already does everything except show the UI.** `scripts/package_launcher.sh`
  runs the platform guard, strips quarantine over the whole package (`:73-87`), checks
  KVM, ports and socket length, does first-run setup, and starts the three services with
  health waits (`:285-318`). It then prints `http://localhost:$UI_PORT` (`:321`) and
  `wait`s. It never opens a browser. Its `stop` trap covers `INT TERM EXIT`, not `HUP`, and
  kills only the direct PIDs (`:273-283`). The backend's per-session `uv run
  dev-mcp-server` and its microVMs are grandchildren, so they can outlive the launcher.
- **The UI is origin-agnostic.** The browser only calls same-origin `/api/*`. Backend and
  MCP URLs are server-side env vars (`ui/app/api/_backend.ts:13,47,51`). There are no cookies
  and no `NEXT_PUBLIC_*` variables. Chat streams over `fetch` + `getReader()`, not
  WebSocket or EventSource. State the UI keeps is `localStorage`/`sessionStorage` (active
  project, session, rail state), keyed on origin.
- **Globus uses a copy-paste code flow** (`UserSettingsModal.tsx:444-515`), with no
  callback route. So the system browser can run the login and nothing needs to return
  to the app.
- **Tab-shaped UI sites.** Eight `target="_blank"` / `window.open` sites exist. External:
  settings token links `:188,:242,:514`, DOI `explorer.tsx:1435`, and skill repos
  `skill-hub/page.tsx:219` and `skills/page.tsx:227`. The agent elicitation URL is
  `page.tsx:908`. Same-origin: PDF `explorer.tsx:1415`, agent file "Download" `page.tsx:1637`,
  and lightbox "Download" `ImageLightbox.tsx:49`. `<a download>` appears at
  `datasets/page.tsx:284,315`.
- **Spike findings (2026-09-22, session `e4e8ae5d`).** Electron 44 works. Rebranding
  (name, bundle ID) invalidates Electron's ad-hoc signature, and `codesign --force --deep
  --sign -` on *that bundle only* restores it, surviving tar, download and unpack. A
  double-clicked quarantined `vista` is blocked by Gatekeeper. That is pre-existing,
  documented, and out of scope. The spike's code was not kept.
- **Re-spike (2026-09-24, task 1.1), with Electron 44.4.5 and @electron/packager 20.3.0.**
  A bundle staged as a plain folder `window/` (no `.app`) runs and keeps its signature.
  macOS then shows a Finder folder icon in the Dock, and "window" in the hover label and
  in Cmd-Tab, because it takes the name from the folder. The menu bar still said VISTA.
  Staged as `window/VISTA.app`, the Dock shows the app's icon and the hover label and
  Cmd-Tab say VISTA. **The layout is therefore `app/window/VISTA.app`.** Separately, the
  About and Quit items used the `package.json` `name` ("vista-window-spike"), so the
  shell's `package.json` needs `"productName": "VISTA"`. Paste and Cmd-Q worked in both
  layouts.
- **`MSB_HOME` ≤ 60 chars** rules out Electron's usual `~/Library/Application Support`
  state location. The shell holds no VISTA state anyway. See W6.

## Goals / Non-Goals

**Goals:**

- The Electron shell is small enough to review in one sitting, and it works with the
  existing services unchanged.
- Every behaviour in `specs/desktop-window` that doesn't need a real display is
  hermetically testable in PR CI.
- `--browser` output and behaviour are byte-for-byte today's, so the smoke test and
  headless users are unaffected.

**Non-Goals:**

- Porting any launcher logic into Electron. Electron does not know the stack exists.
- Rewriting UI links for Electron. Routing lives in the shell. UI edits are limited to
  what also improves the browser case.
- A Linux or Windows window. The known follow-ups are recorded in P1 so they aren't
  rediscovered.

## Decisions

### W1. Electron is a window, the launcher owns the lifecycle

The shell takes `--url=<origin>` and loads it. It has no knowledge of ports, health, or
services. The launcher runs the shell's binary as a foreground child, not via `open`,
which would detach it. When that child exits, the launcher's normal `stop` path runs.

*Alternative rejected — Electron spawns the services (via the bash launcher with a
machine-readable status mode).* This is what double-click would need, and double-click is
out of scope. It would double the surface under review, and it would move error
reporting from the terminal (which exists here) into dialogs.

### W2. Plain JavaScript in `electron/`, no bundler or compile step

`electron/` is a top-level sibling of `ui/`, with its own `package.json` pinning
`electron` (44.x, the spiked version) and `@electron/packager` as dev dependencies.
Sources are ESM `.js` with `// @ts-check` and JSDoc, type-checked by `tsc --noEmit`
against `electron`'s bundled types. Layout:

- `src/main.js`: the app lifecycle, window, and menu.
- `src/routing.js`: pure functions that take an origin and a URL and return one of
  `in-app | external | deny`. They have no Electron import, which is what makes them
  unit-testable.
- `test/`: tests.

*Why not inside `ui/`:* Electron would join the Next dependency graph and risk
the standalone trace. `ui/`'s ESLint and Vitest configs would also have to exclude it.
*Why not TypeScript:* a compile step in both the dev path and the package build
buys little for roughly 200 lines.

### W3. Link routing in the main process, keyed on origin

These three hooks cover every site listed in Context without touching the UI:

- `setWindowOpenHandler`: a same-origin target opens a child `BrowserWindow` with the
  same `webPreferences`, which covers PDFs and images. Anything `http(s)` goes to
  `shell.openExternal` and is denied in-app. Other schemes are denied.
- `will-navigate` / `will-redirect` on every window: off-origin navigation is prevented
  and, if `http(s)`, handed to `shell.openExternal`. This also stops a dropped file
  from replacing the page (`file://` is denied).
- `session.on('will-download')`: the default behaviour shows a save dialog. It is kept,
  with the default path set to `~/Downloads/<suggested name>`.

The two "Download" links that are really `target=_blank` (`page.tsx:1637`,
`ImageLightbox.tsx:49`) gain a `download` attribute when the URL is same-origin. A
browser then saves instead of opening a tab, and Electron raises `will-download`
instead of a child window. That is the only UI edit. `window.confirm`/`alert` already map
to native dialogs and are left alone.

*Alternative rejected — a preload bridge (`window.vista.openExternal`) called from the
UI.* It would give the page an Electron-specific capability for something the main process
can decide by origin alone.

### W4. Locked-down renderer

Settings are `contextIsolation: true`, `sandbox: true` and `nodeIntegration: false`,
with no preload script. `session.setPermissionRequestHandler` denies everything.
`webContents.on('will-attach-webview')` is prevented. DevTools are available only when
`--dev` is passed, which `launch.sh --electron` passes and the package does not.

### W5. Standard shell behaviour

- `app.requestSingleInstanceLock()`: a second instance focuses the first and exits.
- A menu built from roles (`appMenu`, `editMenu`, `viewMenu` minus DevTools unless
  `--dev`, `windowMenu`) is required on macOS for Cmd-C/V/X/A/Z.
- `window-all-closed` quits on every platform, deliberately against macOS convention,
  because the window *is* VISTA (W1).
- The launcher's Ctrl-C/HUP path sends `TERM` to the shell, which then quits normally.
- `--smoke-test`: load the URL, exit 0 on `did-finish-load` with a non-empty title,
  exit 1 on `did-fail-load` or after 30 s. This is used by the package build (B3).
  It takes no single-instance lock, so an open VISTA window can't fail a build.
- `--user-data-dir=<dir>` moves Electron's profile, and with it the single-instance
  lock. Tests use it so they never collide with a real VISTA window. Launchers don't
  pass it.

### W6. Electron state stays out of `~/.vista`

`userData` is left at Electron's default (`~/Library/Application Support/VISTA`). It
holds only Chromium cache and origin storage, not VISTA state, so it doesn't
break the "Mutable state lives outside the artifact" requirement. It also doesn't touch
the `MSB_HOME` budget. *Alternative rejected:* `~/.vista/window` would put browser cache
beside the database and make `rm -rf ~/.vista` the only way to reset it.

### L1. Launcher: window by default, `--browser` to opt out

`package_launcher.sh` gains `--browser`. After the UI health wait, window mode:

1. Reads the window executable's package-relative path from the manifest
   (`window.exe`, written by B1), using the same `sed` field reader as the platform
   guard. The launcher never hard-codes a macOS bundle path. If the field or the file
   is missing (a Linux package), or `can_show_window` says no, it logs one line and
   falls through to browser mode (spec: "Window mode where it cannot run").
   `can_show_window` is one function with a `case "$HOST_OS"`, and only the `macos`
   branch is implemented. That branch checks for a macOS GUI login session, which
   rules out SSH: `launchctl managername` must return `Aqua`, and task 3.2 confirms
   the value it returns over SSH before the design relies on it. Every other OS
   answers "no" until its window exists. Linux adds a `DISPLAY`/`WAYLAND_DISPLAY`
   branch.
2. Runs it with `--url=http://127.0.0.1:$UI_PORT`, backgrounded and added to `PIDS`,
   then `wait`s for *that* PID. When it returns, `exit 0` runs `stop`.

The URL uses `127.0.0.1`, which the UI binds (`:315`), rather than `localhost`, so it
can't resolve to `::1`. The printed browser-mode address changes to match. That is
the one deliberate difference from today's output, and it fixes the same mismatch.

### L2. Stop by process group, trap HUP

The launcher runs `set -m` so each background service leads its own process group,
matching `scripts/launch.sh:3,122`. `stop` sends `kill -- -$pid`, with a fallback to
`kill $pid`, then `TERM` and a 10 s grace period, then `KILL`. `HUP` joins the trap, since
closing Terminal sends it. Verification is by listing processes whose command line
contains the package path after stop (T3).

*Found in task 3.1:* the backend's MCP stdio client starts each sandbox server
(`uv run dev-mcp-server`, its Python and `msb`) in a **new process group**. So the
backend's group kill does not reach it. On TERM the backend closes those clients itself,
which is measured: they are gone within 14 s. The case that matters is a backend that
does not exit, where the final KILL has to reach the sandbox group directly. So `stop`
first collects the process group of every descendant of each service, while the tree
still links them, and it KILLs any of those groups still alive after the grace period.
It never signals the launcher's own group. This was checked with the backend frozen by
SIGSTOP: the launcher exited after 11 s and no sandbox process survived. Job-control
notices ("Terminated: 15") are silenced by sending `stop`'s stderr to `/dev/null` once
it has logged.

*Deferred:* a PID file for recovering from `kill -9` of the launcher itself. The port
preflight already names the conflict in that case.

### L3. Development: `./launch.sh --electron`

This works in `logs` mode only. `tmux` and `terminal` modes reject the flag with a message,
because their lifecycles aren't owned by the script. The script waits for
`http://localhost:3000`, then runs `npx --prefix electron electron electron --dev
--url=http://localhost:3000` as a tracked service, with `localhost` because `next dev`
binds it. Its exit calls `cleanup`. `scripts/build.sh --electron` adds `npm ci` in
`electron/`, so the default build doesn't gain a ~290 MB download. Hot reload works
unchanged, since `next dev`'s HMR WebSocket is same-origin.

### B1–B3. Package build (macOS only)

- **B1** A new `stage_window` step runs after `stage_ui`. It dispatches on the target:
  `stage_window_macos` is implemented, and every other target logs "no window for
  <target>" and returns. The shared part is `npm ci` in `electron/` and
  `@electron/packager` (name `VISTA`, `asar: true`). The macOS branch adds bundle ID
  `gov.ornl.vista`, `electron/assets/icon.icns` if present (otherwise Electron's
  default), and `osxSign: false`. It moves the bundle to `$STAGING/app/window/VISTA.app`,
  keeping the `.app` suffix (see the task 1.1 re-spike in Context), and signs it (B2).
  Signing lives only inside the macOS branch.
- **B2** (inside `stage_window_macos`) `codesign --force --deep --sign - "$STAGING/app/window/VISTA.app"`, then
  `codesign --verify --deep --strict`. This is scoped to that path. The build greps its own
  source to confirm no other `codesign` call exists, so R2 ("do not re-sign `msb`") can't
  regress. The manifest gains a top-level `window` object: `exe`, the executable
  relative to the package root (`app/window/VISTA.app/Contents/MacOS/VISTA` on macOS),
  `electron`, the version, and `bytes`. The size is recorded there rather than as a
  `components` entry, because `components.app` already includes `app/window`. A target
  without a window records `"window": null`. The validator requires `window` on macOS
  and checks that `window.exe` exists and is executable. Packaging itself is
  `electron/scripts/package.js` (`--platform --arch --out`), which never signs, so a
  Linux branch can reuse it.
- **B3** `smoke_test_package.sh` runs `vista --browser` (`:103`). Once the UI is healthy,
  on macOS it also runs `window.exe --smoke-test --url=…`. That proves the
  unpacked, relocated, re-signed shell loads the real UI. The check is skipped with a
  warning, not failed, when the build host has no GUI session. The Linux
  cross-build container never runs it.

### P1. Portability seams for Linux and Windows (follow-up changes, not this one)

The shell (`electron/src/`) and the lifecycle contract are OS-neutral. What each port
still needs:

- **Linux.** Done in the `linux-desktop-window` change, whose design is authoritative.
  - This bullet originally said "Do not use `--no-sandbox`". That change reverses it:
    the window runs with `--no-sandbox` where the host blocks Chromium's sandbox (stock
    Ubuntu 24.04 without VISTA's AppArmor profile, root, some containers), and it says
    so on every start. Its PDFs then go to the system browser. The one-time `sudo`
    profile install turns the sandbox back on, and a later `.deb` installs the same file.
  - It also added the `linux` branch of `can_show_window` (SSH, root, display, and
    missing libraries), `stage_window_linux`, `xvfb-run` in the build container, and a
    crash fallback to browser mode. Process-group stop and `HUP` (L2) applied unchanged.
- **Windows.**
  - The window becomes one more child of the `windows-support` change's PowerShell
    launcher (its D9). That launcher's job object replaces L2's process groups and
    signals. It reads `window.exe` (`app\window\VISTA.exe`) from the same manifest
    field.
  - Done in `build_local_package.sh`'s `stage_window_windows`, since Windows packages
    are built by that script under Git Bash. `vista.ps1` starts the window under `cmd`
    like the services, so its output lands in `window.log` and its exit code (75 when
    another window is open) reaches the launcher.
  - An unsigned `VISTA.exe` meets SmartScreen, the same accepted risk as its unsigned
    `msb.exe`.
  - On Windows, W5's menu becomes a window menu bar, and quit is File → Exit or Alt-F4.

These seams are why L1 reads the executable from the manifest and dispatches the
display check per OS, and why B1 dispatches per target with signing confined to macOS.

### T1–T4. Testing lanes

- **T1 (PR CI, hermetic):** `node --test electron/test/routing.test.js` covers every
  Context site's URL shape: same-origin, other port on 127.0.0.1, `localhost` vs
  `127.0.0.1`, `https`, `mailto`, `file`, `javascript:` and malformed input. Plus
  `tsc --noEmit`. Needs no Electron binary (`ELECTRON_SKIP_BINARY_DOWNLOAD=1`). This is a new
  `electron:test` job in `.gitlab-ci.yml` and a target in `ci-local.sh`.
- **T2 (manual/nightly, needs a display):** Playwright `_electron.launch` against a
  tiny static fixture server. It checks a `_blank` external link, which calls
  `shell.openExternal`, stubbed via `electronApp.evaluate`. It also checks a same-origin
  `_blank` link (child window), off-origin navigation (blocked), a second instance
  (focus, then exit), and `--smoke-test` exit codes. This runs on macOS in the validation lane
  (`docs/validation-lane.md`), not PR CI, because the GitLab Linux runners would need
  `xvfb` and `--no-sandbox`, which isn't the configuration we ship.
- **T3 (manual, package):** start `./vista`, open a session so a sandbox exists, then close
  the window. List processes matching the package path, which should be none, and check the
  three ports, which should be free. Repeat with Ctrl-C and with closing Terminal.
- **T4 (manual, UI walk-through):** walk the spec scenarios in the window. Globus link,
  DOI, PDF, dataset download, agent file download, and paste a key into settings.

## Risks / Trade-offs

- **A `.app` inside `app/window/` is double-clickable for someone who browses there.**
  The plain-folder layout that would have avoided this showed the wrong Dock identity
  (task 1.1). → It is two levels below the package root. In a quarantined unpack,
  double-clicking it gets a Gatekeeper block, which is the same accepted outcome as the
  buried binary below. The README says to start VISTA with `./vista` only.
- **Terminal may prompt for "App Management"** when `xattr -dr` touches an app bundle on
  recent macOS. *Checked in task 1.2 on macOS 26.7 (Chrome download, Terminal.app): no
  prompt.* → If a later macOS starts prompting, the README documents the one-time
  grant.
- **The buried binary is still double-clickable.** Doing so inside a quarantined unpack
  gets a Gatekeeper prompt. Only signing prevents that, and it is accepted.
- **Linux researchers get no window.** Their package behaves exactly as today. The
  launcher's one-line notice says why.
- **The window lends the unauthenticated local API a "native app" feel.** That risk is
  pre-existing and unchanged in substance. It is tracked outside this change.
- **Download size grows by ~121 MB compressed (~287 MB unpacked).** This is accepted, and
  recorded in the manifest.

## Migration Plan

There is no data migration. The shell holds no VISTA state (W6). A researcher who prefers
the browser runs `./vista --browser`. Rollback means reverting the launcher and build
changes; packages built before this change are unaffected.

## Open Questions

- Icon asset: `ui/public` has only wide white-on-transparent Genesis/AmSC lockups, which
  are unsuitable as an app icon. Until someone supplies a square icon, the shell ships
  Electron's default. This is cosmetic, and it changes no tasks: the icon is one path
  in B1.

## Amendment: no browser fallback

The package is desktop-only. `--browser` and the automatic fall-back to printing an address (L1, D5) are removed: a session that cannot show the window is refused up front with the reason, and a window that fails ends the launcher with its exit status. `VISTA_NO_WINDOW=1` starts the services alone, for the build smoke test only. A second window exits 75 (single-instance lock), and the dev window keeps its own userData so it does not contend with an installed one.
