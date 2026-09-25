## 1. Confirm the Ubuntu behaviour on a real host (manual, before any code)

- [ ] 1.1 On an Ubuntu 24.04 desktop (a VM is fine), run a Linux Electron 44.4.5 build of `electron/` against any local page:
  - Build it with `node electron/scripts/package.js --platform linux --arch <arm64|x64> --out DIR`.
  - Serve a page with `python3 -m http.server`.
  - Run `./VISTA --url=http://127.0.0.1:8000/`.

  Record:
  - that it aborts with the `chrome-sandbox` "SUID sandbox helper binary was found, but is not configured correctly" message and a non-zero exit;
  - that `--no-sandbox` opens it;
  - that after installing design D2's profile it opens *without* `--no-sandbox`;
  - the value of `/proc/sys/kernel/apparmor_restrict_unprivileged_userns`;
  - whether it came up under Wayland or XWayland.

  If any of these differs from the design's Context, update D1/D2 before group 2. (manual, needs a display)

  - **Container part done (2026-09-25); the real-host part is deferred until a Linux machine is available.**
    - How it was run: the arm64 build was made on the Mac with `package.js --platform linux --arch arm64`. It ran in scratch `ubuntu:24.04` images on Docker Desktop, which uses a LinuxKit 7.0 kernel with no AppArmor, under `xvfb-run -a … --smoke-test`.
    - Results:
      - **Non-root, namespaces blocked** (Docker's default seccomp standing in for Ubuntu's restriction): `FATAL … The SUID sandbox helper binary was found, but is not configured correctly. Rather than run without sandboxing I'm aborting now. You need to make sure that /w/chrome-sandbox is owned by root and has mode 4755.` with **exit 133** (SIGTRAP). That is non-zero, as D5 needs.
      - **`--no-sandbox`:** loads the page and exits 0. The renderer shares the browser's user namespace, so it is not sandboxed.
      - **Root without the flag:** `Running as root without --no-sandbox is not supported`, exit 133. That confirms D1's rule 1.
      - **D2's profile** parses with Ubuntu 24.04's `apparmor_parser` 4.0.1 (`-Q -K`) as profile `vista-window`, `userns`, mode unconfined.
    - D2 is unchanged. D1's rule 2 now probes `unshare -Ur true` instead of reading the sysctl (decided 2026-09-25). The run showed namespaces blocked with no sysctl present, which the old rule would have missed.
    - **Still deferred (needs a real Ubuntu 24.04 host):**
      - the abort caused by the AppArmor restriction itself;
      - that the loaded profile lets it open without `--no-sandbox`, including that the profile's path glob attaches to `app/window/VISTA`;
      - the sysctl value (the file is absent under LinuxKit);
      - that `unshare -Ur true` exits non-zero on stock Ubuntu 24.04 (design Open Questions);
      - Wayland versus XWayland.
- [ ] 1.2 Repeat the run without `--no-sandbox` on Debian 13 or Fedora, and record that it opens sandboxed with no profile. (manual)
  - **Container part done (2026-09-25):**
    - `debian:trixie` (Debian 13) and `fedora:latest` (Fedora 44) ran non-root with `--security-opt seccomp=unconfined`, so user namespaces were permitted, and with no flag and no profile.
    - Both loaded the page and exited 0, with the renderer in its own user namespace, so it was sandboxed. Ubuntu 24.04 did the same under those conditions.
    - Fedora's image needed `~/root-ca.pem` trusted, because this network's TLS inspection blocks dnf. apt over http was unaffected.
    - **Deferred:** a real desktop session on either distro.

## 2. Window (`electron/`)

- [x] 2.1 Add `electron/linux/window-sandbox` (D1) and `electron/linux/vista-window.apparmor` (D2). The script reads its two paths and its probe command from overridable test variables. Verify with a new `electron/test/window-sandbox.test.js` under `npm test`. It runs the script for these cases and asserts the output and the reason:
  - root;
  - probe succeeds;
  - probe fails with the profile;
  - probe fails without the profile, with the sysctl at 1 (the reason names the install command);
  - probe fails without the profile, with no sysctl (no install command);
  - `unshare` missing (falls back to the sysctl).

  Also run the real script in the 1.1 containers: default seccomp as non-root gives `--no-sandbox` with no install command, and `seccomp=unconfined` gives nothing. The file is included in `electron:test`.
  - **Done (2026-09-25):**
    - Added `electron/linux/window-sandbox` (POSIX `sh`, executable) and `electron/linux/vista-window.apparmor`, which is D2's profile plus a comment header.
    - The test variables are `VISTA_WINDOW_SANDBOX_{UID,PROBE,SYSCTL,PROFILE}`. The script always exits 0; the tests assert this.
    - `npm test` passes 35/35: the 29 routing tests plus 6 new ones in `test/window-sandbox.test.js`, which uses fake `unshare` executables and files. `electron:test` runs `npm test`, so it is covered with no CI change. The tests don't depend on the real user id, so they pass under CI's root container.
    - In the 1.1 `ubuntu:24.04` image, where `/bin/sh` is `dash`:
      - non-root with default seccomp: `--no-sandbox` plus "this host does not allow unprivileged user namespaces … (for example inside a container)";
      - `seccomp=unconfined`: nothing;
      - root: `--no-sandbox` plus "it is running as root".
    - `scripts/package.js` now ignores `linux/`, so the script is not bundled into the app's asar. The build copies it next to the window in 3.2.
- [x] 2.2 `electron/src/main.js`: write `renderer sandbox: on|off` at start. When `--no-sandbox` is set, send `application/pdf` main-frame and sub-frame responses to `shell.openExternal`, and close a child window that loaded nothing else (D3). Verify that `npm run typecheck` passes.
  - **Done (2026-09-25):**
    - The `vista-window: renderer sandbox: on|off (--no-sandbox)` line is printed at start and so lands in `window.log`.
    - `sendPdfsToBrowser` is registered only without the sandbox. It answers a PDF response with `204 No Content` rather than cancelling it, so the page that followed the link stays in place. It closes child windows marked by `did-create-window` that haven't navigated yet.
    - PDFs sent as attachments are left as downloads.
    - D3 has been updated to match. `npm run typecheck` passes.
- [x] 2.3 `electron/test/window.e2e.js`: add a case that launches with `--no-sandbox` and checks that `#pdf-blank` calls the stubbed `openExternal` with `/paper.pdf` and opens no child window. The existing PDF case, which stays sandboxed, still opens one. Make launches add `--no-sandbox` when `process.getuid() === 0`. Verify that `npm run test:e2e` passes on macOS (13 tests). (needs a display)
  - **Done (2026-09-25):** `npm run test:e2e` passes 15/15 on macOS in 5.9 s.
    - The fixture gained `#pdf-nav` and `#pdf-download`.
    - Three new `@no-sandbox`-tagged cases:
      - a new-window PDF goes to `openExternal`, and the child window is closed again;
      - a PDF followed in the window goes to `openExternal`, and the page and its title stay;
      - a `download` link to a PDF is still saved and not opened.
    - The existing sandboxed PDF case still opens a child window. It is skipped as root, where every launch is unsandboxed.
    - As root, every launch gets `--no-sandbox`, including the second-instance and smoke-test spawns.
    - Seen once and not reproduced in 12 later runs of the old or new code: a smoke test against a refused port printed its failure but took 30 s to exit. Watch for it in the 5.1 container run.

## 3. Linux package build

- [x] 3.1 Measure the window's runtime libraries on a bare `ubuntu:24.04` (arm64 is native on this Mac):
  - Unpack the 1.1 build and run `ldd VISTA | grep 'not found'`.
  - Install packages until `ldd` is clean and `xvfb-run -a ./VISTA --no-sandbox --smoke-test --url=…` exits 0.
  - Record the package list in `design.md` D6.
  - Repeat on `fedora:latest` for its package names.
  - **Done (2026-09-25):**
    - On bare `ubuntu:24.04` (arm64), `ldd` over `VISTA` and its bundled `.so` files found 26 missing libraries. `libgtk-3-0t64 libnss3 libasound2t64 libgbm1` make `ldd` clean.
    - With those four plus `xvfb xauth` (and `python3` for the test page), `xvfb-run -a ./VISTA --no-sandbox --smoke-test` loaded the page and exited 0.
    - On `fedora:latest` (Fedora 44): `gtk3 nss alsa-lib mesa-libgbm` make `ldd` clean, and the smoke test passed with `xorg-x11-server-Xvfb xorg-x11-xauth` added. The image needed `~/root-ca.pem` for dnf.
    - The list is recorded in D6.
- [x] 3.2 `scripts/build_local_package.sh`: add `stage_window_linux` (D7): the arch map, `npm ci` with `ELECTRON_SKIP_BINARY_DOWNLOAD=1`, `package.js --platform linux`, the move to `app/window/`, copying `window-sandbox` and `vista-window.apparmor`, and `WINDOW_EXE=app/window/VISTA`. Run the GitHub probe in preflight on Linux, and require the window in the manifest validator on Linux. Verify by running the validator's logic against a staged manifest, with and without the window, and checking that `stage_window_macos` output is unchanged (`bash -n`, plus a macOS build in 7.1).
  - **Done (2026-09-25):**
    - `stage_window_linux` is as specified. `aarch64` or `arm64` maps to arm64 and `x86_64` to x64; the files go in with `install -m 755/644`.
    - The GitHub probe now runs for macOS and Linux; `codesign` and the signing-call count stay macOS-only.
    - The validator requires a window on Linux, and on Linux also an executable `window-sandbox` next to it.
    - `bash -n` passes. The diff has no hunk inside `stage_window_macos`.
    - The validator's Python was extracted and run against fake staged packages. Six cases, all as expected:
      - Linux with no window fails;
      - Linux with a window but no `window-sandbox` fails;
      - Linux with both passes;
      - macOS with no window fails;
      - macOS with a window passes;
      - another OS with no window passes.
    - The real run of `stage_window_linux` comes with 3.3's build. Its steps were reproduced by hand in the updated build image (see 3.3).
- [x] 3.3 `scripts/Dockerfile.build`: add `xvfb`, `xauth` and the 3.1 libraries. `scripts/smoke_test_package.sh`: add the Linux branch of "the window loads the UI" (D7), using a display if one is present, otherwise `xvfb-run -a`, otherwise skipping with a reason, and passing `window-sandbox`'s arguments. Verify with `./scripts/build_in_docker.sh --platform linux/arm64 --vector-store … --payload …` (using `~/.vista`), which must end with "the window loads the UI" passing. Then run it for `linux/amd64` and record whether the check is reliable under emulation. If it isn't, implement the skip-when-emulated from the Risks section. Pass `--ca-bundle` if this network's TLS inspection blocks the image build. Fix `build_in_docker.sh:221`'s `local` outside a function if that path is hit.
  - **Done (2026-09-25).**
    - `Dockerfile.build` gains `xvfb xauth libgtk-3-0t64 libnss3 libasound2t64 libgbm1`.
    - The smoke test's Linux branch:
      - it asks `window-sandbox` next to the window for its arguments, and appends the reason to `window-smoke.log`;
      - it uses `xvfb-run -a` when there is no `DISPLAY` or `WAYLAND_DISPLAY`;
      - it skips with a reason when there is neither a display nor `xvfb-run`.
    - The empty-array idiom was checked under macOS's bash 3.2 with `set -u`.
    - The updated image was built for `linux/arm64` with the org CA. Inside it, as root, the window was staged as `stage_window_linux` does and the smoke test's window step was run exactly:
      - `window-sandbox` → `--no-sandbox` ("running as root");
      - "ok the window loads the UI";
      - `window.log` shows `renderer sandbox: off (--no-sandbox)`;
      - `linux/` is not inside `app.asar`.
    - **Full builds (2026-09-25),** both from `2dc8e49` with `--payload ~/.vista-build/vista-data --vector-store ~/.vista-build/rag_db` and `VISTA_BUILD_CA_BUNDLE=~/root-ca.pem`. The tokens came from Sam's git URL rule and keychain.
      - `linux/arm64` produced `vista-0.1.0+2dc8e49-linux-aarch64.tar.gz` (2.50 GB).
      - `linux/amd64`, under emulation in about 13 minutes, produced `…-linux-x86_64.tar.gz` (2.60 GB).
      - Both smoke tests ended "all checks passed", with **"ok the window loads the UI"**. The two skips are the existing ones: retrieval (no `/dev/kvm`) and Globus (no credential).
      - Both manifests have `window: {exe: app/window/VISTA, electron: 44.4.5}`, about 300 MB unpacked.
      - The window check passed under emulation, so no skip-when-emulated was added. That is one run, so watch it in later amd64 builds.
      - `build_in_docker.sh:221` was not hit, because the CA bundle was supplied.
    - Seen along the way, outside this change: packager's prune leaves a few small files and empty scope directories under `node_modules` in `app.asar`, on macOS builds as well, so `package.js`'s "prune leaves no node_modules at all" is not quite true. The cost is kilobytes.

## 4. Launchers

- [x] 4.1 `scripts/package_launcher.sh`: add the `linux` branch of `can_show_window`, covering SSH, root, display and the `ldd` library check (D4, D6). Each "no" gives its reason and browser mode. Verify in the 3.3 Linux package inside a container:
  - no `DISPLAY` → "no graphical display";
  - `SSH_CONNECTION=x` → "remote shell session";
  - root → "does not run as root";
  - with a library removed (`LD_LIBRARY_PATH` is not enough; use a container without `libgtk-3-0t64`) → that library is named.
  Each keeps the services running. (container, `VISTA_ALLOW_NO_KVM=1`)
  - **Done (2026-09-25).**
    - The checks run in this order: SSH (`SSH_CONNECTION` or `SSH_TTY`), root, display (`DISPLAY` or `WAYLAND_DISPLAY`), then `ldd` on the window executable. The library reason names the first five missing libraries, adds "and N more" for the rest, and gives the Ubuntu/Debian and Fedora/RHEL package lists.
    - Verified against the 3.3 arm64 package unpacked into a Docker volume, with this launcher mounted over its `vista`, `VISTA_ALLOW_NO_KVM=1`, and a non-root user except in the root case:
      - no display → "there is no graphical display (neither DISPLAY nor WAYLAND_DISPLAY is set)";
      - `SSH_CONNECTION=x` → "this is a remote shell session";
      - root → "the window does not run as root";
      - a bare `ubuntu:24.04` image (no GTK, NSS, ALSA or GBM) → the missing libraries by name, plus the install lines.
    - In every case the launcher printed the address, the services answered on 3000, TERM stopped it, and ports 3000/8000/8001 were free afterwards.
    - Found and fixed: `stage_window_linux` shipped `app/window` as 0700, because `mktemp -d` makes it so and `mv` keeps the mode. Anyone other than the user who unpacked the package got "This package has no VISTA window". It now runs `chmod 755` on the directory. The two 3.3 archives in `dist/` predate the fix.
- [ ] 4.2 `scripts/package_launcher.sh`: start the window with `window-sandbox`'s arguments, and on branch 4 print the reason and the D2 install command, with the package's absolute path. Verify on the Ubuntu 24.04 desktop from 1.1 as part of 7.2.
  - **Implemented; the Ubuntu desktop check stays with 7.2.**
    - The launcher runs the `window-sandbox` next to the window, sending its stderr to `logs/window-sandbox.log`. It prints each line of that log and passes the stdout words to the window.
    - Container checks against the same package, with Xvfb:
      - default seccomp, where namespaces are blocked → the "host does not allow unprivileged user namespaces" reason, and `window.log` shows `renderer sandbox: off (--no-sandbox)`;
      - `--security-opt seccomp=unconfined` → no message and `renderer sandbox: on`;
      - `VISTA_WINDOW_SANDBOX_SYSCTL` pointing at a file containing `1` (Ubuntu's restriction, simulated) → the Ubuntu message, with `sudo install -m 644 '<package>/app/window/vista-window.apparmor' /etc/apparmor.d/vista-window` and the `apparmor_parser` line using the package's absolute path.
- [x] 4.3 `scripts/package_launcher.sh`: keep the window's exit status (D5). On 0, exit; on non-zero, log the failure and `window.log`, print the address and wait as in browser mode. Verify on macOS with a built package:
  - `kill -SEGV <window pid>` leaves the services answering, and the launcher prints the address;
  - Ctrl-C afterwards stops everything;
  - quitting the window normally still exits and leaves ports 3000/8000/8001 free.
  (manual, `sandbox`)
  - **Done (2026-09-25).**
    - The launcher now keeps the window's exit status:
      - 0 exits as before;
      - non-zero logs "The VISTA window stopped unexpectedly (exit N); see …/window.log." and "The services are still running.", then falls through to "VISTA is running at …" and waits as in browser mode.
    - `stop()` sets `STOPPING=true`. Bash resumes after an interrupted `wait` once its trap has run, so without that flag a Ctrl-C would read as a crash.
    - **macOS:** `vista-0.1.0+0f0eac8-macos-arm64` was built with `--archive-format none`, reusing the payload and vector store. It was run with the ports moved to 23000/28000/28001, because 8000 was taken locally. Ctrl-C was sent as SIGINT to the launcher's own process group, as a terminal does.
      - `kill -SEGV <window>` gave "stopped unexpectedly (exit 139)" and the address, and the services kept answering. Ctrl-C then stopped everything, with the ports free.
      - A normal quit (SIGTERM to the window, which `main.js` turns into `app.quit()`) made the launcher exit, with the ports free and no "unexpectedly" line.
      - Ctrl-C with the window open gave a clean exit, the ports free, and no "unexpectedly" line.
    - **Linux** (container, arm64 package, Xvfb): the same three results. The crash case had the renderer sandbox on (`seccomp=unconfined`), the quit case had it off (default seccomp), and a stop there was sent as TERM.
- [x] 4.4 `scripts/launch.sh`: `WINDOW_CMD` appends `window-sandbox`'s arguments on Linux and prints the reason (D8). The same exit-status rule is not needed there, since a developer restarts. Verify on macOS that `./launch.sh logs --electron` is unchanged. Verify on Linux as part of 7.2.
  - **Done for macOS (2026-09-25); Linux stays with 7.2.**
    - `WINDOW_SANDBOX` is `'$(./linux/window-sandbox)'` on Linux, and is expanded by the window's own shell after `cd electron`. Elsewhere it is empty.
    - On macOS, `WINDOW_CMD` was evaluated from both `0f0eac8^` and `0f0eac8` and compared after word splitting. The only difference is a space before the final `;`, so the command run is the same. `bash -n` passes. The full `./launch.sh logs --electron` was not started, because port 8000 was already in use by an unrelated local process.

## 5. Linux e2e tests

- [x] 5.1 `docs/validation-lane.md`: add the command that runs `npm run test:e2e` in `mcr.microsoft.com/playwright:v1.62.1-noble` under `xvfb-run -a` (D9). Verify by running it exactly as written. There is no CI job; D9 says why.
  - **Done (2026-09-25).**
    - This replaces the planned advisory `electron:e2e` CI job, which was dropped from this change.
    - The command mounts `electron/` and puts an anonymous volume over `node_modules`, so the host's macOS modules are neither used nor changed.
    - Run as documented on this Mac:
      - the first attempt failed at Electron's binary download (`fetch failed`), because of this network's TLS inspection;
      - with `-v ~/root-ca.pem:/ca.pem:ro -e NODE_EXTRA_CA_CERTS=/ca.pem`, **14 passed and 1 skipped in 22 s**. The skip is the sandboxed PDF case, which cannot run as root. All three `@no-sandbox` PDF cases passed.
      - This is the first time the e2e tests have run on Linux. The CA workaround is in the doc.

## 6. Docs

- [ ] 6.1 `README.md` "Running a prebuilt package": the Linux window, with SSH and `--browser` falling back; the Ubuntu sandbox message and the one-time profile install; the window's libraries per distribution (from 3.1); and PDFs opening in the browser while unsandboxed. Verify that every command in it was run in groups 1–4.
- [ ] 6.2 `docs/validation-lane.md`: the "VISTA window" lane covers Linux (D9's manual list); the container command is already there (5.1). `AGENTS.md`: `electron/linux/` and the sandbox rule. Add a line to the release notes and checklist saying the bundled Electron version is reviewed each release. Update `electron-desktop-shell`'s `design.md` P1 Linux bullet to point at this change, replacing "Do not use `--no-sandbox`". Verify with `openspec validate linux-desktop-window --strict`.

## 7. Validation (manual)

- [ ] 7.1 Build the macOS package and run its smoke test ("the window loads the UI" passes). Repeat 4.3's crash and stop checks on the built archive.
- [ ] 7.2 On the Ubuntu 24.04 desktop, unpack the `linux-<arch>` archive and run D9's manual list with `./vista`:
  - the sandbox is off with the message;
  - installing the profile turns it on;
  - PDFs go to the browser only while unsandboxed;
  - Wayland and X11;
  - SSH gives the address;
  - the window crash leaves the services running;
  - after each of the three stop methods, nothing is left running.

  Repeat the sandbox-on check on Debian 13 or Fedora. Use `VISTA_ALLOW_NO_KVM=1` if the VM has no nested virtualisation. Record the results here.
