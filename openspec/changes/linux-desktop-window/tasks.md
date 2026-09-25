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

- [ ] 2.1 Add `electron/linux/window-sandbox` (D1) and `electron/linux/vista-window.apparmor` (D2). The script reads its two paths and its probe command from overridable test variables. Verify with a new `electron/test/window-sandbox.test.js` under `npm test`. It runs the script for these cases and asserts the output and the reason:
  - root;
  - probe succeeds;
  - probe fails with the profile;
  - probe fails without the profile, with the sysctl at 1 (the reason names the install command);
  - probe fails without the profile, with no sysctl (no install command);
  - `unshare` missing (falls back to the sysctl).

  Also run the real script in the 1.1 containers: default seccomp as non-root gives `--no-sandbox` with no install command, and `seccomp=unconfined` gives nothing. The file is included in `electron:test`.
- [ ] 2.2 `electron/src/main.js`: write `renderer sandbox: on|off` at start. When `--no-sandbox` is set, send `application/pdf` main-frame and sub-frame responses to `shell.openExternal`, and close a child window that loaded nothing else (D3). Verify that `npm run typecheck` passes.
- [ ] 2.3 `electron/test/window.e2e.js`: add a case that launches with `--no-sandbox` and checks that `#pdf-blank` calls the stubbed `openExternal` with `/paper.pdf` and opens no child window. The existing PDF case, which stays sandboxed, still opens one. Make launches add `--no-sandbox` when `process.getuid() === 0`. Verify that `npm run test:e2e` passes on macOS (13 tests). (needs a display)

## 3. Linux package build

- [ ] 3.1 Measure the window's runtime libraries on a bare `ubuntu:24.04` (arm64 is native on this Mac):
  - Unpack the 1.1 build and run `ldd VISTA | grep 'not found'`.
  - Install packages until `ldd` is clean and `xvfb-run -a ./VISTA --no-sandbox --smoke-test --url=…` exits 0.
  - Record the package list in `design.md` D6.
  - Repeat on `fedora:latest` for its package names.
- [ ] 3.2 `scripts/build_local_package.sh`: add `stage_window_linux` (D7): the arch map, `npm ci` with `ELECTRON_SKIP_BINARY_DOWNLOAD=1`, `package.js --platform linux`, the move to `app/window/`, copying `window-sandbox` and `vista-window.apparmor`, and `WINDOW_EXE=app/window/VISTA`. Run the GitHub probe in preflight on Linux, and require the window in the manifest validator on Linux. Verify by running the validator's logic against a staged manifest, with and without the window, and checking that `stage_window_macos` output is unchanged (`bash -n`, plus a macOS build in 7.1).
- [ ] 3.3 `scripts/Dockerfile.build`: add `xvfb`, `xauth` and the 3.1 libraries. `scripts/smoke_test_package.sh`: add the Linux branch of "the window loads the UI" (D7), using a display if one is present, otherwise `xvfb-run -a`, otherwise skipping with a reason, and passing `window-sandbox`'s arguments. Verify with `./scripts/build_in_docker.sh --platform linux/arm64 --vector-store … --payload …` (using `~/.vista`), which must end with "the window loads the UI" passing. Then run it for `linux/amd64` and record whether the check is reliable under emulation. If it isn't, implement the skip-when-emulated from the Risks section. Pass `--ca-bundle` if this network's TLS inspection blocks the image build. Fix `build_in_docker.sh:221`'s `local` outside a function if that path is hit.

## 4. Launchers

- [ ] 4.1 `scripts/package_launcher.sh`: add the `linux` branch of `can_show_window`, covering SSH, root, display and the `ldd` library check (D4, D6). Each "no" gives its reason and browser mode. Verify in the 3.3 Linux package inside a container:
  - no `DISPLAY` → "no graphical display";
  - `SSH_CONNECTION=x` → "remote shell session";
  - root → "does not run as root";
  - with a library removed (`LD_LIBRARY_PATH` is not enough; use a container without `libgtk-3-0t64`) → that library is named.
  Each keeps the services running. (container, `VISTA_ALLOW_NO_KVM=1`)
- [ ] 4.2 `scripts/package_launcher.sh`: start the window with `window-sandbox`'s arguments, and on branch 4 print the reason and the D2 install command, with the package's absolute path. Verify on the Ubuntu 24.04 desktop from 1.1 as part of 7.2.
- [ ] 4.3 `scripts/package_launcher.sh`: keep the window's exit status (D5). On 0, exit; on non-zero, log the failure and `window.log`, print the address and wait as in browser mode. Verify on macOS with a built package:
  - `kill -SEGV <window pid>` leaves the services answering, and the launcher prints the address;
  - Ctrl-C afterwards stops everything;
  - quitting the window normally still exits and leaves ports 3000/8000/8001 free.
  (manual, `sandbox`)
- [ ] 4.4 `scripts/launch.sh`: `WINDOW_CMD` appends `window-sandbox`'s arguments on Linux and prints the reason (D8). The same exit-status rule is not needed there, since a developer restarts. Verify on macOS that `./launch.sh logs --electron` is unchanged. Verify on Linux as part of 7.2.

## 5. CI

- [ ] 5.1 `.gitlab-ci.yml`: add an `electron:e2e` job in the test stage on `mcr.microsoft.com/playwright:v1.62.1-noble`, with `allow_failure: true`, an npm cache keyed on `electron/package-lock.json`, `npm ci`, and `xvfb-run -a npm run test:e2e` (D9). Verify by running those commands in that image locally, as root, from the committed tree (all e2e tests pass), then with the MR pipeline once pushed. Do not add it to `scripts/ci-local.sh`'s default targets, because it needs the Playwright image or a display.

## 6. Docs

- [ ] 6.1 `README.md` "Running a prebuilt package": the Linux window, with SSH and `--browser` falling back; the Ubuntu sandbox message and the one-time profile install; the window's libraries per distribution (from 3.1); and PDFs opening in the browser while unsandboxed. Verify that every command in it was run in groups 1–4.
- [ ] 6.2 `docs/validation-lane.md`: the "VISTA window" lane covers Linux (D9's manual list) and the new `electron:e2e` job. `AGENTS.md`: `electron/linux/` and the sandbox rule. Add a line to the release notes and checklist saying the bundled Electron version is reviewed each release. Update `electron-desktop-shell`'s `design.md` P1 Linux bullet to point at this change, replacing "Do not use `--no-sandbox`". Verify with `openspec validate linux-desktop-window --strict`.

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
