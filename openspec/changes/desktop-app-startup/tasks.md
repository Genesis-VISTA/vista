Checked items 1.1, 2.1–2.5, 3.1–3.7, 4.1, 4.2 and 7.1–7.4 were done on `mac-native-startup`
(Feiyi Wang, `f04feb5`) and carry over. Its signing tasks (old 1.2, 1.3, 4.3, 4.4 and 5.5) are
dropped by design D7; `signing-spike.md` keeps their record.

## 1. Rebase and baseline

- [x] 1.1 Inventory the macOS package's executables, signatures and `msb`'s entitlements
  (`signing-spike.md`). Kept as the reference for any later signing lane.
- [x] 1.2 Rebase `desktop-app-startup` onto `desktop-icon` (!145). Resolve `README.md`,
  `scripts/build_local_package.sh`, `scripts/smoke_test_package.sh`, `electron/src/main.js` and
  `electron/scripts/package.js`, keeping both the icon wiring and the startup flow. Rebase again
  onto `main` once !145 is squash-merged. *Rebased onto `1cb2d1a`; the re-rebase onto `main`
  waits for !145.* `github-release-builds` is archived on this branch, so the `release-builds`
  deltas apply.
- [x] 1.3 After the rebase, run `cd electron && npm test`, the Electron typecheck,
  `scripts/tests/package_launcher_supervised_test.sh` and `./scripts/ci-local.sh install`, and
  record the results. Fix the pre-existing second-instance e2e test that expects exit 0 where the
  launcher contract returns 75.
  *2026-10-06, macOS 26: `npm test` 59/59, typecheck clean, supervised and dev-launcher tests
  pass, `ci-local.sh install` and `electron` pass, e2e 21/21. The startup e2e's first-run test
  was flaky (a cold window missed the 90 ms `resources` running state); the fake launcher now
  holds each phase 300 ms.*

## 2. Supervised launcher: macOS and Linux

- [x] 2.1 Add `--supervised --progress=jsonl` to `scripts/package_launcher.sh`: no window, same
  preflight, setup, health waits, steady-state wait and process-group cleanup.
- [x] 2.2 Emit protocol-v1 events for preflight, resources, sandbox image, MCP, backend, UI
  readiness, failure and stopping, with skipped completion for idempotent work.
- [x] 2.3 Add stable error codes and safe fields: port conflict, invalid state path, missing
  component, resource extraction, image import, health timeout. No environment values.
- [x] 2.4 Add the parent-stdin EOF watcher that requests normal cleanup.
- [x] 2.5 Add hermetic tests in `scripts/tests/package_launcher_supervised_test.sh`.
- [x] 2.6 Lift the macOS-only restriction on supervised mode for Linux. On Linux, keep
  `can_show_window` and `window-sandbox` out of supervised mode; the pre-window launcher (5.1)
  owns them. Extend 2.5's tests to run the Linux path.
  *Linux without KVM fails with `virtualisation-unavailable`. The tests run every supervised
  scenario as macOS and as Linux, pass on macOS bash 3.2 and in `ubuntu:24.04`, and now run in CI
  (`launcher:test`, `./scripts/ci-local.sh launcher`).*
- [x] 2.7 Make the event labels platform-neutral ("Checking this computer").

## 3. Supervised launcher: Windows

- [x] 3.1 Add `-Supervised -Progress jsonl` to `scripts/package_launcher.ps1` (`vista.ps1`),
  emitting the same phases, states, codes and fields as the bash mode, in the same order. Human
  output goes to stderr.
- [x] 3.2 Treat end-of-file on stdin as the stop request and run the existing stop, which takes
  each service's tree with `taskkill /T`. Keep the kill-on-close job object as the backstop.
- [x] 3.3 Add hermetic tests in `scripts/tests/package_launcher_supervised_test.ps1` with fake
  services: event order, the port-conflict failure, and the stdin stop leaving no process. Run
  them from `./scripts/ci-local.sh` under `pwsh`, and in the Windows CI job.
  *Passes under pwsh on macOS and in `mcr.microsoft.com/powershell:7.5-ubuntu-24.04` (stand-ins
  for cmd, taskkill and tar); GitLab `launcher:test` and the release workflow's Windows package
  job run it. Windows-only codes: `virtualisation-unavailable` (msb doctor) and
  `package-path-too-long`. Not yet run on Windows itself: the release build or Sam's machine.*

## 4. The startup application

- [x] 4.1 Split Electron argument and lifecycle handling into modules, and take the
  single-instance lock before starting the launcher. Keep `--url`, `--dev`, `--smoke-test` and
  the Linux renderer-sandbox behaviour.
- [x] 4.2 Add the startup page and its narrow preload bridge (progress, Retry, Open Logs, Copy
  Diagnostics, Quit).
- [x] 4.3 Implement the protocol-v1 parser and state machine; reject unsupported or malformed
  input.
- [x] 4.4 Spawn the launcher in supervised mode, map its lifecycle to the startup window, and
  block Retry until the previous child has exited.
- [x] 4.5 Hand off to the existing sandboxed main window at 1280 × 860 on UI readiness.
- [x] 4.6 Make close and Quit stop the launcher, await bounded cleanup, and leave no process or
  port.
- [x] 4.7 Cover first run, skipped work, failure, retry, quit during startup, hand-off and second
  launch with a fake launcher (`electron/test/`).
- [x] 4.8 Resolve the package root on Linux and Windows (`app/window/` two levels below it),
  alongside `resolveMacPackage`, from the manifest's per-platform `entrypoint` and
  `diagnostic_launcher`. Extend `electron/test/package-root.test.js`.
- [x] 4.9 Start the right launcher per platform: `vista` on macOS and Linux. On Windows, run
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File vista.ps1 -Supervised -Progress
  jsonl` hidden, after unblocking `vista.ps1` and checking for `AllSigned`, which reports as
  its own failure code. On Windows, stop by closing stdin, then `kill()` after the grace
  period. Cover each in `electron/test/launcher-controller.test.js`.
- [x] 4.10 Startup page: show the VISTA icon (`assets/icon.png`), follow the UI's light and dark
  palette, use platform-neutral copy, and keep the native frame on every platform.
  *The page declares the UI's own tokens; `appearance.test.js` checks each against
  `ui/app/globals.css` in both themes. It follows the OS appearance, like the window frame.*
- [ ] 4.11 Show the Linux sandbox notice from the pre-window launcher (5.1) in the startup window
  when the window runs without the renderer sandbox.

## 5. Linux desktop integration

- [ ] 5.1 Add `electron/linux/vista-app`, the pre-window launcher (design D9). It checks the
  display and refuses as root, runs the `ldd` library check and `window-sandbox`, starts the
  window with `--startup`, and keeps the one early-crash retry without the sandbox. It reports
  failures that leave no window through `notify-send` when available, and always in
  `~/.vista/logs/window.log`. Ship it beside the window in `build_local_package.sh`.
- [ ] 5.2 Add a hermetic test for `vista-app`, like `electron/test/window-sandbox.test.js`,
  covering the root refusal, missing libraries, the sandbox flag, and the retry.
- [ ] 5.3 Add `"desktopName": "vista.desktop"` to `electron/package.json`, and confirm the window's
  WM_CLASS is `VISTA`.

## 6. Packaging and release checks

- [x] 6.1 Stage `VISTA.app` at the macOS package root and keep `vista` as the diagnostic launcher;
  record both in `manifest.json`.
- [x] 6.2 Make the application validate its package root before starting anything, with a
  graphical error when separated.
- [x] 6.3 Record `entrypoint` and `diagnostic_launcher` in the Linux and Windows manifests too
  (`app/window/vista-app` and `vista`; `app/window/VISTA.exe` and `vista.cmd`).
  *Done with 4.8, which reads them. The build checks that the Linux entrypoint exists from 5.1,
  which adds it.*
- [ ] 6.4 In `scripts/smoke_test_package.sh`, replace the macOS-only Finder launch with one
  supervised check on every platform (design D12): events in order to `ui`/`ready` with the
  right URL; a forced port conflict giving `preflight`/`failed`/`port-conflict`; a stop leaving
  no process or port. Keep every existing check.
- [ ] 6.5 Keep the window as the only signed path on macOS (ad hoc), and keep the preflight that
  refuses any other `codesign` in the build.

## 7. Installers

- [x] 7.1 Add the checkout-backed `VISTA Dev.app` (`scripts/build_mac_dev_app.sh`), with its own
  bundle identifier and `~/.vista-dev`.
- [x] 7.2 Add `scripts/mac_dev_launcher.sh`, which reports the startup protocol for the source
  services, without live HPC submission.
- [x] 7.3 Keep the developer build independent of release corpora, runtimes, Apple credentials
  and `amscrot-py`.
- [x] 7.4 Document and test the dev-app build and its Finder launch
  (`scripts/tests/mac_dev_launcher_test.sh`).
- [ ] 7.5 `scripts/install.sh`: on macOS, install into `/Applications/VISTA` when it can write
  there (the existing `/Applications/VISTA` if there is one, else `/Applications`), otherwise into
  `~/Applications/VISTA`, saying why (design D6). Then register the app with `lsregister -f` and
  `mdimport`. No symlink or alias in `/Applications`. On Linux, install into
  `~/.local/share/vista/app`, write `~/.local/share/applications/vista.desktop` and install the
  icon into `hicolor`. Keep the `~/.local/bin/vista` link. Start VISTA with `open` on macOS and
  `vista-app` on Linux.
- [ ] 7.6 `scripts/install.ps1`: point the Start-menu shortcut at `app\window\VISTA.exe` with
  `--startup`, and start VISTA the same way.
- [ ] 7.7 Both installers refuse while VISTA is running (its UI port answers as VISTA, or a VISTA
  window process exists), with "Close VISTA, then run this again". They change nothing in that
  case. On macOS this covers a VISTA running from either install folder.
- [ ] 7.8 Both installers remove earlier-layout package folders after a successful install, and
  never touch `VISTA_HOME`. On macOS that includes the researcher's own `~/Applications/VISTA`
  after an install into `/Applications/VISTA`, and never a `/Applications/VISTA` it did not
  install into. Retry a removal once (Finder can write a `.DS_Store` mid-removal) and report what
  is left rather than failing the install.
- [ ] 7.9 Extend `scripts/test_install.sh` and `scripts/test_install.ps1`. Cover the fixed
  folders, including the macOS choice between `/Applications/VISTA` and `~/Applications/VISTA`
  (with the system folder faked writable and not), the desktop entry and shortcut, the refusal
  while running, the removal of the old layout, and that a re-run downloads nothing.

## 8. Release notes and documentation

- [ ] 8.1 `.github/release-notes.md`: manual macOS steps unpack into `/Applications/VISTA` (or
  `~/Applications/VISTA` without administrator rights) and open `VISTA.app`; keep the curl-not-browser note and the `xattr`/System Settings fallback.
  Manual Linux and Windows steps end at the app-menu or Start-menu entry, with the diagnostic
  launcher as the alternative.
- [ ] 8.2 `README.md`: each platform starts from the application; `vista`/`vista.cmd` are for
  diagnostics; the whole folder must stay together.
- [ ] 8.3 `docs/validation-lane.md`: a manual macOS checklist (Dock icon and its size against
  neighbours, listed in the Apps view and in Finder's Applications, Finder and Spotlight launch,
  curl install into `/Applications/VISTA`, first and later run, error and Retry,
  second launch, quit cleanup, immediate restart). **Manual; keep out of PR CI.** It replaces the
  signed/quarantined walk-through.
- [ ] 8.4 Replace `AGENTS.md`'s "the launchers own its lifetime" description of `electron/` with
  the app-first model and the diagnostic launchers.

## 9. Acceptance

- [ ] 9.1 Run `./scripts/ci-local.sh` (all targets) and a GitHub release build by hand on this
  branch; all three platforms pass, including 6.4.
- [ ] 9.2 On a Mac, install the hand-built release with the one-line installer and run 8.3's
  checklist. **Manual macOS validation; keep out of PR CI.**
- [ ] 9.3 Run `openspec validate desktop-app-startup` and fix anything it reports.
- [ ] 9.4 Before opening the MR, on a real Windows machine: run
  `scripts\tests\package_launcher_supervised_test.ps1`, install the hand-built release with
  `install.ps1`, start VISTA from the Start menu, and quit it, leaving no process. **Manual;
  keep out of PR CI.**
