## Context

See `proposal.md` — Why. Decision identifiers (D1–D9) are for review comments. This
change builds on `electron-desktop-shell` (its W, L, B and T decisions) and carries out
the Linux half of its P1.

Current state and constraints that shape the approach:

- **The Linux seams exist but are empty.**
  - `can_show_window` in `scripts/package_launcher.sh` answers "VISTA has no window on
    linux yet" for every non-macOS host.
  - `stage_window` in `build_local_package.sh` logs "no VISTA window for …".
  - `electron/scripts/package.js` accepts `--platform linux` but has never been run.
  - The manifest validator requires a window only on macOS.
- **A crashing window takes VISTA down, on both platforms.** After starting the window,
  the launcher runs `wait "$WINDOW_PID" || true; exit 0`, and the EXIT trap stops every
  service. That is right when the researcher closes the window. It is wrong when the
  window aborts, which is what Chromium does at once on Ubuntu 24.04 without a sandbox.
- **Why the sandbox fails on Ubuntu.** Chromium's Linux renderer sandbox is two layers:
  - namespaces, which give the renderer no filesystem, network or process view, and are
    created through unprivileged user namespaces;
  - seccomp-bpf, a filter on which kernel calls the renderer may make.
  Ubuntu 23.10+ sets `kernel.apparmor_restrict_unprivileged_userns=1`. That grants
  capabilities inside a new user namespace only to programs whose AppArmor profile says
  `userns`. Chromium's fallback is the setuid `chrome-sandbox` helper, which a tarball
  unpacked by a user cannot have. So Chromium aborts rather than run unsandboxed, unless
  it is given `--no-sandbox`. Debian 13, Fedora and RHEL 10 do not restrict user
  namespaces.
- **What the renderer renders.** The window loads only VISTA's own origin (W3). But that
  origin serves content VISTA does not author:
  - knowledge-base PDFs, through `/api/knowledge-bases/<slug>/publications/<file>`
    (`ui/app/knowledge-bases/explorer.tsx:1412`);
  - agent-produced images, and agent-produced HTML in `SandboxedHtmlCard`'s iframe;
  - markdown from model output.
  W4's settings (`contextIsolation`, `sandbox: true`, no Node integration) are separate
  from the OS sandbox and stay in force without it. They keep VISTA's own page script
  away from Node; the OS sandbox is what contains a renderer that has been exploited
  through a browser-engine bug.
- **Build hosts.** Linux packages are built by `scripts/build_in_docker.sh` in an
  `ubuntu:24.04` container (`scripts/Dockerfile.build`) that runs as root, has no display,
  and lacks Electron's GUI libraries. From an Apple Silicon Mac, `linux/arm64` runs natively
  and `linux/amd64` under emulation.

## Goals / Non-Goals

**Goals:**

- Linux packages open the window wherever a desktop session exists, including stock
  Ubuntu 24.04.
- The sandbox is off only on hosts that force it off, and that is visible every time,
  never silent.
- A native `.deb` later only has to install one file this change already ships, and then
  the `--no-sandbox` path stops being taken without any code change.
- The window's behaviour tests can be run on Linux, in a container, from the validation
  lane.

**Non-Goals:**

- Running `sudo` on the researcher's behalf.
- A window icon, a `.desktop` entry or app-menu integration. They need native packaging.

## Decisions

### D1. The launcher decides the sandbox, from the host's policy, before starting the window

One small script, `electron/linux/window-sandbox`, shipped as `app/window/window-sandbox`,
prints the extra arguments the window needs on this host. It prints nothing, or
`--no-sandbox` followed by a one-line reason on stderr. The rule:

1. Running as root: `--no-sandbox`, because Chromium refuses to run as root otherwise.
   Only the build's smoke test reaches this (D7), since the launcher does not open a
   window for root (D4).
2. `unshare -Ur true` succeeds: nothing (sandbox on). This asks the host directly whether
   an unprivileged user namespace with capabilities can be made, which is what Chromium's
   namespace layer needs. It takes milliseconds.
3. `/etc/apparmor.d/vista-window` exists: nothing (sandbox on, D2). This has to come
   after the probe but before giving up, because on Ubuntu `unshare` has no profile of
   its own and fails even when VISTA's is installed.
4. Otherwise: `--no-sandbox`. The reason depends on
   `/proc/sys/kernel/apparmor_restrict_unprivileged_userns`, which is now read only to
   choose the message:
   - `1`: Ubuntu's restriction, followed by the D2 install command;
   - anything else: "this host does not allow unprivileged user namespaces" (for example
     a container, or `user.max_user_namespaces=0`). No VISTA step can fix that, so none
     is named.

If `unshare` is not installed (it ships in util-linux on every mainstream distro, so this
is unlikely), rule 2 falls back to the sysctl: absent or not `1` means nothing.

The package launcher, `./launch.sh --electron` and the smoke test all call this script,
so the rule exists once. The launcher prints the reason, and the install command where
there is one, on every start that takes branch 4.

- *Alternative rejected: read the sysctl alone for rule 2* (the first draft). It only
  knows Ubuntu's mechanism. In task 1.1's container run, Docker's default seccomp profile
  blocked user namespaces for a non-root user with no such sysctl present. That rule
  would have said "sandbox on", the window would have aborted (exit 133), and D5 would
  have given browser mode instead of a window. `unshare -Ur true` exited 1 there and 0
  where namespaces were permitted.

- *Alternative rejected: always `--no-sandbox` on Linux.* It turns the sandbox off on
  Debian, Fedora and RHEL, where it works, and would stay that way after a `.deb` fixes
  Ubuntu.
- *Alternative rejected: try with the sandbox and retry without it on a crash.* It
  disables the sandbox for any start-up failure, not just the policy one, and costs a
  failed start every time on Ubuntu.
- *Alternative rejected: decide in `main.js` with
  `app.commandLine.appendSwitch('no-sandbox')`.* Chromium's Linux zygote reads the sandbox
  switches before the app's JavaScript runs, so a switch appended there is not reliably
  honoured. A launcher argument is also visible in `ps` and in `window.log`.
- *Checking for the file, not the loaded profile*: `aa-status` needs root. A profile file
  present but never loaded makes Chromium abort, and D5 turns that into browser mode, with
  `window.log` naming the cause.

### D2. The AppArmor profile ships in the package; installing it is the researcher's choice

`electron/linux/vista-window.apparmor`, shipped next to `VISTA` in `app/window/`:

```
abi <abi/4.0>,
include <tunables/global>

profile vista-window /**/{app/window/VISTA,electron/node_modules/electron/dist/electron} flags=(unconfined) {
  userns,
  include if exists <local/vista-window>
}
```

This is the form Ubuntu uses for the Chrome and VS Code profiles. `unconfined` restricts
nothing further; the profile exists only to grant `userns`. The `/**/` glob covers every
unpack location and version, so installing it is a one-time step. The second path covers
the development window (D8). The launcher prints the install command with the package's
own absolute path:

```
sudo install -m 644 <package>/app/window/vista-window.apparmor /etc/apparmor.d/vista-window
sudo apparmor_parser -r /etc/apparmor.d/vista-window
```

- *Trade-off:* any executable at a path ending in `app/window/VISTA` or
  `electron/node_modules/electron/dist/electron` gains `userns`. That is what a `.deb`'s
  fixed-path profile avoids, and why the `.deb` remains the proper fix.
- *Alternative rejected: setuid `chrome-sandbox`.* It has to be redone for every unpack,
  and it puts a setuid-root binary in a directory the user owns.

### D3. Without the sandbox, PDFs go to the system browser

When `main.js` sees `app.commandLine.hasSwitch('no-sandbox')`, it registers a
`session.webRequest.onHeadersReceived` handler. For a main-frame or sub-frame response
whose `Content-Type` is `application/pdf`, the handler calls `shell.openExternal` on its
URL and does two more things:
- it rewrites the response to `204 No Content`, so the page that followed the link
  stays where it was instead of showing a blocked-load error page;
- it closes the child window if the response was the only thing that window ever loaded.
  `did-create-window` marks each new child, and its first `did-navigate` clears the mark.

A PDF sent with `Content-Disposition: attachment`, or fetched through a `download` link,
is a download and is left alone. The PDF then opens in the researcher's browser, where the
browser's own sandbox applies. The URL is on `127.0.0.1`, which that browser can reach.

- *Keyed on content type, not URL,* so a PDF reached by any route is covered, not just the
  knowledge-base one.
- *Why only PDFs:* PDFium is the largest parser in the window of files VISTA did not
  create, and moving it out costs one click of a different kind. Images, markdown and
  `SandboxedHtmlCard` cannot move out without removing the feature (see Risks).
- `main.js` also writes one line to `window.log` at start, `renderer sandbox: on` or
  `off (--no-sandbox)`, so a report from the field says which mode it was in.

### D4. `can_show_window` on Linux

A `linux` branch, checked in this order:

1. `SSH_CONNECTION` or `SSH_TTY` is set: no window, "this is a remote shell session". This
   matches macOS, where an SSH session is not Aqua. X forwarding would put the window on
   the far end, and an SSH user wants the address anyway.
2. Root: no window, "the window does not run as root".
3. Neither `WAYLAND_DISPLAY` nor `DISPLAY` is set: no window, "no graphical display".
4. D6's library check.

Electron chooses Wayland or X11 itself. Either works, including XWayland.

### D5. A window that exits with an error falls back to browser mode

The launcher keeps the window's exit status instead of discarding it:

- **0 (closed or quit):** exit, and the EXIT trap stops everything, as today.
- **Non-zero:** log "the VISTA window stopped unexpectedly (exit N); see
  `$LOGS/window.log`", then print the address and `wait` for the services exactly as in
  browser mode. Ctrl-C and closing the terminal still stop everything.

A trapped signal interrupts `wait` and runs `stop` first, so a stop from the terminal
never reaches the fallback. This applies on macOS too. There is no time window, because a
window crash mid-session should not take the services down either.

- *Alternative rejected: fall back only within the first N seconds.* It adds a timer and a
  threshold to tune, and still stops VISTA when the window crashes after the threshold.

### D6. Missing window libraries are named, not crashed into

Before starting the window, the launcher runs
`ldd "$PACKAGE/$WINDOW_EXE" | grep 'not found'`. If any library is missing, it names those
libraries and continues in browser mode (D4 step 4). The README lists the Debian/Ubuntu
and Fedora package names for the set, which task 3.1 measures on a bare `ubuntu:24.04`
rather than copying a list from elsewhere. `ldd` exists wherever glibc does, and the
glibc 2.39 floor already requires glibc.

Measured in task 3.1 with Electron 44.4.5 on arm64. A bare image lacks 26 libraries, and
four packages supply all of them. With those four plus a display, the window's smoke test
passes:

| | Debian / Ubuntu | Fedora / RHEL |
|---|---|---|
| GTK 3 (pulls in X11, cairo, pango, ATK, GLib, xkbcommon, D-Bus) | `libgtk-3-0t64` | `gtk3` |
| NSS / NSPR | `libnss3` | `nss` |
| ALSA | `libasound2t64` | `alsa-lib` |
| GBM | `libgbm1` | `mesa-libgbm` |

A desktop install already has all four. They matter for a minimal server or a container.

### D7. Building and smoke-testing the Linux window

- **Staging.** `stage_window_linux` maps `x86_64`→`x64` and `aarch64`→`arm64` and runs
  `npm ci` in `electron/` with `ELECTRON_SKIP_BINARY_DOWNLOAD=1`, since packager fetches
  the Linux zip itself. It then runs `package.js --platform linux`, moves the output to
  `app/window/`, and adds `window-sandbox` and `vista-window.apparmor`. It sets
  `WINDOW_EXE=app/window/VISTA`. No signing.
- **Manifest and preflight.** The validator requires a window on Linux as on macOS. The
  GitHub reachability probe runs on both.
- **Container.** `Dockerfile.build` gains `xvfb`, `xauth` and the libraries measured in
  task 3.1. That serves the smoke test only; the package itself carries none of them.
- **Smoke test.** The Linux branch of "the window loads the UI":
  - runs directly if a display is present, otherwise under `xvfb-run -a`, and skips with
    a reason if neither is available;
  - passes the arguments `window-sandbox` prints. In the container that is
    `--no-sandbox`, because the container runs as root and Docker's default seccomp
    profile blocks user namespaces anyway.
  - The check therefore proves the window loads the UI. It does not prove the sandbox
    works on a real host; T-manual (D9) covers that.

### D8. Development window

`launch.sh`'s `WINDOW_CMD` appends `$(electron/linux/window-sandbox)` on Linux and prints
its reason. The profile's second path (D2) lets a developer turn the sandbox on for the
development binary with the same one-time step.

### D9. Tests

- **PR CI (hermetic).** `electron:test` is unchanged. A shell test covers
  `window-sandbox`'s four branches, using a test override for the two paths it reads.
- **The e2e tests on Linux: a validation-lane command, not a CI job.**
  - The command runs on `mcr.microsoft.com/playwright:v1.62.1-noble`, which has Node,
    `xvfb` and Chromium's libraries, and matches the pinned `@playwright/test`.
  - It runs `npm ci` and then `xvfb-run -a npm run test:e2e`.
  - The tests pass `--no-sandbox` when running as root.
  - A new e2e case launches with `--no-sandbox` and checks that the fixture's PDF link
    calls the stubbed `shell.openExternal` and opens no child window. The existing PDF
    case keeps covering the sandboxed path.
  - **Why no CI job (decided 2026-09-25).**
    - The logic most likely to change is where a link goes, and that is in
      `routing.js`, whose tests are already required in PR CI.
    - Each Linux package build already checks that the window loads the UI.
    - `main.js` is small and rarely changes.
    - An advisory job would cost every MR a large image pull and one more job, and we
      don't know yet whether the runners can reach `mcr.microsoft.com`.
    - Add the job if the window starts changing often. The container command is
      already the job's script.
- **Manual (validation lane)**, on a real Ubuntu 24.04 desktop and one of Debian 13 or
  Fedora. A VM is fine: without nested virtualisation, `VISTA_ALLOW_NO_KVM=1` is
  acceptable for window-only checks.
  - The sandbox is off with the message on stock Ubuntu, on after installing the profile,
    and on without any step on Debian or Fedora.
  - PDFs open in the system browser when the sandbox is off.
  - The window over Wayland and over X11.
  - SSH gives the address.
  - A missing library is named.
  - `kill -SEGV` on the window leaves the services running.
  - The three stop methods leave nothing running.

## Risks / Trade-offs

- **[Risk] With the sandbox off, a browser-engine exploit in the renderer runs with the
  researcher's account**, including `~/.vista` tokens and SSH keys. → It is limited to
  hosts that force it off (D1), announced on every start, and fixed by one documented
  command (D2). The window loads only VISTA's origin (W3), and PDFs leave the window
  (D3). Recorded as an accepted risk, like the Windows change's unsigned `msb.exe`.
- **[Risk] `SandboxedHtmlCard` runs agent-produced HTML and script in the unsandboxed
  renderer.** Its iframe `sandbox` attribute isolates it from the page, but not from an
  engine bug. → Accepted with the above. If this is judged too much, the follow-up is to
  route those cards to the system browser too when the sandbox is off, which would be a
  spec change.
- **[Risk] The pinned Electron ages, and an unsandboxed old engine is the worst case.** →
  README release notes list the Electron version, and bumping it becomes a release
  checklist item (task 6.2).
- **[Risk] The AppArmor profile's glob is broader than a fixed path** (D2). → Documented.
  The `.deb` replaces it.
- **[Risk] Chromium under `linux/amd64` emulation on Apple Silicon may crash or time out
  in the smoke test.** → Measured in task 3.3. If it fails, the window check is skipped
  when the build is emulated, with the reason printed, and `linux/arm64` still runs it.
- **[Risk] The profile-file check misjudges a host**, for example one whose profile was
  installed under another name. → The window runs unsandboxed with the message, which is
  the safe direction. A file present but not loaded falls to D5.
- **[Trade-off] Linux archives grow by about 100 MB compressed** (about 270 MB
  unpacked).

## Migration Plan

None beyond the behaviour change. Linux packages open the window by default, and
`./vista --browser` restores today's behaviour. Rolling back means shipping the previous
build: the manifest's `window: null` path is unchanged. When a `.deb` exists, it installs
`vista-window.apparmor` to a fixed-path variant, and D1's branch 3 then keeps the sandbox
on with no code change.

## Open Questions

- *(Resolved in task 3.3.)* How reliably the smoke test runs under `linux/amd64`
  emulation. The full amd64 build's window check passed, so there is no emulation skip.
  That rests on one run.
- That `unshare -Ur true` fails on stock Ubuntu 24.04 with the restriction on. This is
  expected, because the restriction denies capabilities in the new namespace, so writing
  `uid_map` fails. It is confirmed on a real host in task 1.1. If it succeeds there, rule
  2 would say "sandbox on" and D5 would give browser mode. In that case, add the sysctl
  back as a condition on rule 2.
