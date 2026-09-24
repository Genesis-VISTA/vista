## 1. Close the spike's open checks (manual, macOS, before any code)

- [x] 1.1 Build a throwaway rebranded Electron 44 bundle, ad-hoc re-sign it, and stage it as a plain folder `window/` (no `.app` suffix). Run its binary against a running dev stack. Verify and record what the Dock and menu bar show for name and icon. If either is generic, switch B1 to `app/window/VISTA.app` in `design.md` before group 4. (manual, needs a display)
  - **Result (2026-09-24):** the plain folder showed a Finder folder icon in the Dock, and "window" in the hover label and in Cmd-Tab; the menu bar said VISTA. `window/VISTA.app` showed the Electron icon, with VISTA in the hover label and Cmd-Tab. B1 has been switched to `app/window/VISTA.app`. About/Quit read "vista-window-spike" in both layouts (from `package.json` `name`), which is fixed by `productName` in 2.1. Paste and Cmd-Q worked. The bundle is 303 MB unpacked and 131 MB as `.tar.gz`.
- [x] 1.2 Tar that folder with a stub `vista` that runs `xattr -dr com.apple.quarantine`, download it through Safari, and unpack it in Finder. Run `./vista` from **Terminal.app**. Record whether macOS asks for "App Management" permission. If it does, add the one-time grant to README task 6.2. (manual)
  - **Result (2026-09-24, macOS 26.7):** tested with the `app/window/VISTA.app` layout and `productName` set. It was downloaded through **Chrome** (quarantine record `0081;…;Chrome;…`) and unpacked in Finder. Running `./vista` from Terminal.app gave **no permission prompt**. The quarantine attribute went from set to `none`, the window opened on the VISTA UI, About and Quit read VISTA, and closing the window returned exit 0. No README note is needed. Safari sets the same attribute but was not separately exercised.

## 2. Electron shell (`electron/`)

- [x] 2.1 Create `electron/package.json` (ESM, `"productName": "VISTA"` so About/Quit say VISTA, `electron@44` and `@electron/packager` as dev dependencies, `typescript` for checking), `electron/tsconfig.json` (`checkJs`, `noEmit`) and `.gitignore` entries. Verify that `cd electron && npm ci && npx tsc --noEmit` passes on an empty `src/main.js`.
- [x] 2.2 Implement `electron/src/routing.js`: pure `classify(origin, url)` (one rule serves both pop-ups and navigation, so there is no `kind`) returning `in-app | external | deny` per design W3. Verify with `electron/test/routing.test.js` under `node --test`, covering same origin, other port, `localhost` vs `127.0.0.1`, `https`, `mailto`, `file:`, `javascript:` and malformed URLs.
  - **Done:** 29 cases pass. Non-http(s) schemes are refused before the origin check, because a `blob:` URL reports its creator's origin.
- [x] 2.3 Implement `electron/src/main.js`: `--url`/`--dev`/`--smoke-test` argument parsing, single-instance lock, locked-down `webPreferences` (W4), permission handler that denies everything, `setWindowOpenHandler` / `will-navigate` / `will-redirect` wired to `classify` and applied to child windows too, `will-download` defaulting to `~/Downloads`, role-based menu (W5), and quit on `window-all-closed` and on `SIGTERM`. Verify manually with `npx electron . --dev --url=http://localhost:3000` against `./launch.sh`. The window loads, Cmd-C/V work in a settings field, the DOI link opens Safari, and a PDF opens a child window.
  - **Done (2026-09-24), driven by Playwright against `./launch.sh logs` in this worktree, with the system browser and save dialog stubbed.** The window lands on `/projects` ("Vista Console"). The settings "Get a key" and "Get a token" links go to the system browser, and so does a knowledge-base DOI link; the window stays put each time. A dataset download saves `BeF2-NaF-UF4_phase_diagram.png` without opening a page. The menu has the full Edit menu, and no DevTools without `--dev`. Paste in the real UI was confirmed by Sam in the 1.1 spike, which used the same role menu. The seeded knowledge base has no flat-file PDFs ("indexed only"), so the PDF child window is covered by 2.5's fixture PDF instead: it renders in Electron's viewer. **Left for the 7.1 walk-through:** a real Finder file drop, and the Globus "Open in browser" link, which needs a Globus login to start and follows the same `_blank` path as the other external links.
- [x] 2.4 Implement `--smoke-test` (exit 0 on load with a non-empty title, 1 on failure or after 30 s). Verify that the exit code is 0 against the dev stack and 1 against a closed port.
  - **Done:** exit 0 ("VISTA fixture") against the fixture server (the dev stack was down), 1 on `ERR_CONNECTION_REFUSED`, and 2 for a non-http `--url`. Smoke-test mode takes no single-instance lock, so an open VISTA window cannot fail a build.
- [x] 2.5 Add `electron/test/window.e2e.js`: Playwright `_electron.launch` against a static fixture server in the test. Cover external `_blank` (stubbed `shell.openExternal` called, no window), same-origin `_blank` (child window), off-origin navigation (URL unchanged), a second instance (exits, the first is focused) and smoke-test exit codes. Verify with `npx playwright test -c electron/playwright.config.js` on macOS. **Not PR CI**: needs a display (validation lane).
  - **Done:** 12/12 pass (`cd electron && npm run test:e2e`, ~8 s). Also covers a same-origin PDF rendering in a child window (not downloaded), `window.open`, a same-origin redirect to another site, `file:` links, downloads, and the page having no `require`/`process` and denied notifications. Tests use `--user-data-dir` so they never collide with a real VISTA window.

## 3. Launchers

- [x] 3.1 `scripts/package_launcher.sh`: `set -m`; stop by process group with TERM, a 10 s grace period, then KILL; add `HUP` to the trap (L2). Verify by starting `./vista --browser` from a built package, opening a chat so a sandbox starts, and pressing Ctrl-C. Then `pgrep -fl "$PACKAGE"` is empty and ports 3000/8000/8001 are free. Repeat by closing the Terminal window. (manual, `sandbox`)
  - **Done (2026-09-24),** on an APFS clone of the 2026-09-08 package (`~/.e2e/v1/...a94a263`, left untouched) with ports 3100/8100/8101 and `VISTA_HOME=~/.vista-g3`, under macOS `/bin/bash` 3.2. A sandbox was started by a chat with a *dummy* inference key (the agent starts its tools, then the model call fails). **Finding:** the sandbox server runs in its own process group, not the backend's (see design L2), so `stop` also collects every descendant's group. Results:
    - Ctrl-C: everything stopped within 14 s, sandbox included.
    - Backend frozen with SIGSTOP, then Ctrl-C: the launcher exited after 11 s and nothing survived.
    - HUP in window mode: everything stopped within 1 s.
    - In every case the ports were free afterwards. Closing a real Terminal.app window is left for 7.1.
- [x] 3.2 `scripts/package_launcher.sh`: add a `--browser` flag and window mode as the default (L1). Read `window.exe` from `manifest.json` with the existing `sed` field reader, and never hard-code the bundle path. Add a `can_show_window` function with `case "$HOST_OS"` (implement only `macos`; other OSes answer no). Fall back to browser mode with one line when the field or the file is missing or `can_show_window` says no (first confirm that `launchctl managername` over SSH ≠ `Aqua`). Run the shell as a tracked child and exit when it exits. Print `127.0.0.1` in browser mode. Update `--help`. Verify that closing the window leaves nothing running (same check as 3.1), that Ctrl-C closes the window, and that `ssh localhost ./vista` falls back. (manual, `sandbox`)
  - **Done:** with the real `electron/` window staged into the clone at `app/window/VISTA.app` (signed) and `window.exe` in its manifest. Results:
    - The default mode opened the window. Quitting the app made the launcher exit 0 within 1 s with nothing left.
    - `--browser` prints `http://127.0.0.1:3100`.
    - Without `window.exe` (the unmodified package), it prints the address.
    - With a stand-in `launchctl` reporting `Background` on PATH, it prints "Not opening the VISTA window: this session has no display (launchctl reports 'Background', not Aqua; over SSH, for example)", keeps the services up, starts no window, and cleans up on Ctrl-C.
    - The real value over SSH is **not yet confirmed**: key auth to localhost is not set up. Left for 7.1.
    - Task 4.4's `--browser` in `smoke_test_package.sh` was done here, so the build never opens a window.
- [x] 3.3 `scripts/launch.sh` and `scripts/build.sh`: add an `--electron` flag (L3). `build.sh --electron` runs `npm ci` in `electron/`, and `launch.sh --electron` in `logs` mode runs the shell with `--dev` as a service whose exit triggers `cleanup`. `tmux`/`terminal` modes reject the flag. Verify that `./launch.sh --electron` opens the window, an edit to `ui/app/page.tsx` hot-reloads in it, and closing it stops all three services.
  - **Done:**
    - `./launch.sh tmux --electron` exits 1 with a reason.
    - `./launch.sh logs --electron` ran `build.sh --electron` (`npm ci` in `electron/`) and opened the dev window on `http://localhost:3000`, which served real requests.
    - Closing the window: "Shutting down...", exit 0, all three ports free.
    - `--no-build` then Ctrl-C: the window and stack stopped at once.
    - In dev the window runs from Electron's stock app bundle, so the Dock says "Electron"; only the packaged build is branded.
    - **Hot reload in the window is not yet checked by eye** (Sam).

## 4. Package build (macOS)

- [x] 4.1 `scripts/build_local_package.sh`: add a `stage_window` step after `stage_ui` that dispatches on the target (B1). `stage_window_macos` is implemented; other targets log "no window for <target>" and return. It runs `npm ci` in `electron/` and `@electron/packager` (name `VISTA`, bundle ID `gov.ornl.vista`, `asar`, `osxSign: false`), then moves the result to `$STAGING/app/window/VISTA.app` (per 1.1). Add Electron's download host to the preflight. Verify with `./scripts/build_local_package.sh --check` and a build that has `--skip-smoke-test --keep-staging`: `app/window/VISTA.app` exists.
  - **Done (2026-09-24):** full build, `./scripts/build_local_package.sh --payload ~/.vista/vista-data --vector-store ~/.vista/knowledge-bases/molten-salt-papers/rag_db --keep-staging` (reused store, fresh sandbox image). `stage_window` logged `window : 287M (Electron 44.4.5)`, and `app/window/VISTA.app` is in the staging tree. The archive is 2.28 GB. Packaging goes through the new `electron/scripts/package.js`. Preflight probes github.com and requires `codesign` on macOS.
- [x] 4.2 Inside `stage_window_macos` only, ad-hoc sign `app/window/VISTA.app` (`codesign --force --deep --sign -`, then `--verify --deep --strict`). Add a build-time guard that fails if any other `codesign` invocation exists in the script. Verify that `codesign --verify` on the staged `msb` is unchanged from before the step (compare `codesign -dv` output).
  - **Done:** preflight counts `codesign …--sign` lines in the script and requires exactly 1. The packaged `msb` and the worktree's own `msb` have the same CDHash (`a7a41be2…`), are both ad-hoc, both carry `com.apple.security.hypervisor`, and both pass `codesign --verify`.
- [x] 4.3 Manifest: add a top-level `window` object (`exe`, relative to the package root; `electron`, the version; `bytes`, not in `components`, because `components.app` already includes it), or `null` on targets without one. The validator requires `window` for `macos-*` and checks that `window.exe` exists and is executable. Verify that the manifest validator passes on macOS and that a Linux build via `build_in_docker.sh --check` still validates without `window`.
  - **Done:** the built manifest has `"window": { "exe": "app/window/VISTA.app/Contents/MacOS/VISTA", "electron": "44.4.5", "bytes": 301268992 }`, and the validator passed in the build. The build's validator was also run directly against four variants:
    - Linux with `window: null`: passes.
    - macOS with `null`: fails with "manifest has no window on macOS".
    - macOS naming a missing executable: fails, naming the path.
    - The real macOS manifest: passes.
  - **Not run: a real Linux build.** `build_in_docker.sh --check` could not build its image on this network: curl exit 60 fetching Node inside the container, i.e. TLS interception. The script's own proxy-CA guidance then crashed with `line 221: local: can only be used in a function`. That is an existing bug; the script is unchanged on this branch.
  - The built package itself was also run in default mode, on alternate ports with a fresh `VISTA_HOME`: its `app/window/VISTA.app` opened on `http://127.0.0.1:3100`, and quitting it stopped everything within 1 s.
- [x] 4.4 `scripts/smoke_test_package.sh`: run `vista --browser` (already done in 3.2). On macOS with a GUI session, also run the shell with `--smoke-test` against the unpacked UI; otherwise warn and skip. Verify that a full `./scripts/build_local_package.sh` passes its smoke test, including the window check.
  - **Done:** after `vista --browser` (3.2), the smoke test reads `window.exe` and runs it with `--smoke-test` against the relocated UI. It skips when the package has no window or there is no GUI session. The build's smoke test reported `ok   the window loads the UI`, along with all the other checks.

## 5. UI (minimal)

- [x] 5.1 Add `download` to the same-origin "Download" links at `ui/app/page.tsx:1637` and `ui/components/ImageLightbox.tsx:49` (external URLs unchanged), and update the comment at `ImageLightbox.tsx:44`. Verify that `cd ui && npm run lint && npm run typecheck && npm test` pass, that the browser saves the file rather than opening a tab, and that in the window a save dialog appears.
  - **Done (2026-09-24):** a new `ui/lib/file-links.ts` `fileLinkProps(url)` gives VISTA's own paths (`/…`, not `//…`) `download` and **no** `target`, so the window can never open a child window for them. Other URLs keep `target="_blank" rel="noreferrer"`. It decides from the string alone, so the server and client renders agree. It is used at both sites; the lightbox comment is updated.
    - Tests: new `ui/tests/file-links.test.ts`, and `ImageLightbox.test.tsx` now asserts `download` for VISTA's files and `_blank` for external images.
    - `npm run lint` passes with 0 errors; its 3 warnings are the existing `<img>` ones. `npm run typecheck` passes, and `npm test` passes 115/115.
    - The window's handling of a same-origin `<a download>` is covered by 2.5's download test.
    - **By eye, left for 7.1:** the real chat artifact and lightbox links, which need an agent run that produces a file.

## 6. CI and docs

- [x] 6.1 Add an `electron:test` job to `.gitlab-ci.yml` (`ELECTRON_SKIP_BINARY_DOWNLOAD=1 npm ci`, `tsc --noEmit`, `node --test test/routing.test.js`) and an `electron` target to `scripts/ci-local.sh`. Verify with `./scripts/ci-local.sh electron test` locally and on the MR pipeline.
  - **Done:** `.gitlab-ci.yml` gains `electron:typecheck` (lint stage) and `electron:test` (test stage, required) on `node:22-slim`, with `ELECTRON_SKIP_BINARY_DOWNLOAD=1` and an `npm-electron` cache keyed on `electron/package-lock.json`. The YAML parses (pyyaml).
    - `./scripts/ci-local.sh electron` passes: typecheck plus 29/29 tests. The target is also part of `all`.
    - The job's commands passed in a clean `node:22-slim` container from the committed tree, with no Electron binary downloaded.
    - The MR pipeline itself has not run yet; it needs the push.
- [x] 6.2 README: under "Running a prebuilt package", describe the window, `--browser`, "run from Terminal, don't double-click", and the App Management note if 1.2 found one. Under development, describe `./launch.sh --electron` and `./scripts/build.sh --electron`. Add a T2/T3/T4 walk-through to `docs/validation-lane.md`. Also update the "Common Commands" section in `AGENTS.md`. Verify that the docs render and the commands in them run as written.
  - **Done:**
    - README "Running a prebuilt package": start from a terminal, don't double-click; the window, and what stops it; `--browser` and the no-display and Linux fallback; links, PDFs and downloads; `window.log` and the window's own cache location. No App Management note, per 1.2.
    - README "Launch": `./launch.sh logs --electron` and `build.sh --electron`.
    - `docs/validation-lane.md`: a new "VISTA window" lane covering the e2e command, the three-way stop and cleanup check, and the spec walk-through.
    - `AGENTS.md`: a "VISTA window (Electron)" section and the `electron` ci-local target.
    - Every command in them was run in groups 2–4 or here.

## 7. End-to-end validation (manual, macOS, validation lane)

- [ ] 7.1 On a fresh account or with `VISTA_HOME` pointing at an empty directory, download a built package through Safari, unpack it in Finder, and run `./vista` in Terminal.app. Walk the `desktop-window` spec scenarios (T4): Globus link in the system browser, then paste the code back; DOI; PDF child window; dataset and agent-file downloads; paste an API key; second launch focuses the first. Then do T3 cleanup for all three stop paths. Record the results in this change. (manual, `live`, `sandbox`)
