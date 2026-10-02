## 1. Prove the macOS distribution gate

- [x] 1.1 On a copy of a completed macOS package, inventory every executable assessed by signing,
  notarization and Gatekeeper; record the current signature, CDHash and complete entitlements of
  the bundled `msb` binary.
- [ ] 1.2 Build a Developer ID/hardened-runtime `VISTA.app`, notarize and staple it, and determine
  whether the existing sibling runtime may execute unchanged. If not, prove an explicit inside-out
  signing order that preserves `msb`'s entitlements. Do not use recursive package-wide
  `codesign --deep --force`.
- [ ] 1.3 Put the candidate distribution through a quarantine/download round trip; verify
  `spctl --assess`, stapler validation, `msb doctor`, and one real sandbox spawn. **Manual macOS
  release validation; keep out of PR CI.** Stop this change and document the blocker if the app,
  Gatekeeper and sandbox cannot all pass without bypass instructions.

## 2. Define the supervised launcher contract

- [x] 2.1 Add an internal `--supervised --progress=jsonl` mode to
  `scripts/package_launcher.sh`, accepted only on macOS, that skips window creation but retains the
  existing preflight, setup, health waits, steady-state wait and process-group cleanup.
- [x] 2.2 Emit protocol-v1 JSON-lines events for preflight, resources, sandbox image, MCP, backend,
  UI readiness, failure and stopping. Emit skipped completion for idempotent work; do not parse or
  duplicate service log output.
- [x] 2.3 Add stable error codes and safe fields for port conflict, invalid state path, missing
  package component, resource extraction, image import and service health timeout. Never include
  environment values or credentials.
- [x] 2.4 Add a parent-stdin EOF watcher that requests the launcher's normal cleanup when its
  supervising application disappears.
- [x] 2.5 Add hermetic shell tests under `scripts/tests/` using fake services/curl to assert event
  ordering, failure events, cancellation and cleanup. Assert the ordinary CLI's output and Linux
  execution path remain unchanged.

## 3. Add the macOS startup application flow

- [x] 3.1 Split Electron argument/lifecycle handling into testable modules and acquire the
  single-instance lock before starting the launcher. Preserve current `--url`, `--dev`,
  `--smoke-test` and Linux renderer-sandbox behaviour.
- [x] 3.2 Add the local startup HTML/CSS/JavaScript and a narrowly scoped preload bridge for
  progress, retry, Open Logs, Copy Diagnostics and Quit. Use system fonts, light/dark appearance,
  normal traffic lights and reduced-motion support.
- [x] 3.3 Implement the protocol-v1 parser and launch state machine. Reject unsupported or malformed
  protocol input; keep stderr in `window.log` and retain only sanitized diagnostics in memory.
- [x] 3.4 Spawn the diagnostic launcher in supervised mode, map its lifecycle to the startup UI,
  and prevent Retry until the previous child and its cleanup have completed.
- [x] 3.5 On UI readiness, create the existing sandboxed main window hidden at its established
  1280 × 860 default, show it on `ready-to-show`, and close the startup window. Confirm the compact
  preparation window does not set the working window's size and the main renderer has no startup
  preload or IPC capability.
- [x] 3.6 Make close and Cmd-Q show a stopping state when necessary, terminate the launcher, await
  bounded cleanup and leave no service, sandbox process or occupied VISTA port.
- [x] 3.7 Extend `electron/test/` and `electron/test/window.e2e.js` with a fake launcher covering
  first run, skipped work, failure, retry, quit during startup, transition, and second launch
  during both startup and the main interface.

## 4. Package the macOS entrypoint

- [x] 4.1 Stage `VISTA.app` at the macOS package root and keep `vista` as the diagnostic launcher.
  Record both roles in `manifest.json`; do not change Linux or Windows layouts.
- [x] 4.2 Make the application resolve and validate its sibling package root before starting
  anything, with a graphical error when it has been separated from the runtime.
- [ ] 4.3 Replace the current ad-hoc macOS window-signing step with the proven Developer ID,
  hardened-runtime, notarization and stapling flow. Keep credentials outside the repository and
  make release mode fail closed when they are absent.
- [ ] 4.4 Add build assertions for the intended signatures and entitlements, notarization ticket,
  Gatekeeper assessment and real sandbox spawn. Preserve the existing relocated-package retrieval
  smoke test.
- [x] 4.5 Update `scripts/smoke_test_package.sh` so macOS also launches `VISTA.app`, observes the
  startup-to-main transition without Terminal, quits it and verifies all processes and ports are
  gone. Keep signing/Gatekeeper checks in the macOS release lane, not hermetic PR CI.

## 5. Documentation and acceptance

- [x] 5.1 Update `README.md` so macOS users launch the top-level app; retain the command-line path
  only for diagnostics. State that the whole unpacked folder must remain together.
- [x] 5.2 Add the signed/quarantined macOS release walk-through to `docs/validation-lane.md`,
  including first run, later run, error/retry, second launch, close cleanup and immediate restart.
- [x] 5.3 Run `cd electron && npm test` and the hermetic Electron e2e suite with the fake launcher.
  The 57 unit/type checks and all six startup e2e scenarios pass. The combined legacy window
  suite remains 19/20 because its pre-existing second-instance test expects exit 0 while the
  documented launcher contract intentionally returns `EX_TEMPFAIL` (75).
- [ ] 5.4 Run the relevant package-launcher and build-script tests on macOS and Linux; verify the
  Linux package launcher and window-sandbox outputs are unchanged.
- [ ] 5.5 Build a signed release candidate on macOS, download and unpack it as a fresh user would,
  launch it from Finder, complete first-run preparation, open one agent session so a sandbox exists,
  quit, and verify no matching process or occupied port remains. **Manual macOS release validation;
  keep out of PR CI.**
- [x] 5.6 Validate all OpenSpec artifacts when the `openspec` CLI is available and record any
  environment limitation if it remains unavailable.

## 6. Add a thin macOS developer application

- [x] 6.1 Add a checkout-backed `VISTA Dev.app` build that packages only Electron, uses a separate
  bundle identifier and state directory, and is ad-hoc signed for local use.
- [x] 6.2 Add a supervised source launcher that reports the existing startup protocol, starts the
  source services without live HPC submission, and cleans them up when the application closes.
- [x] 6.3 Keep the developer build independent of release corpora, vector stores, sandbox archives,
  bundled runtimes, Apple credentials, and the private `amscrot-py` dependency.
- [x] 6.4 Document and validate the one-command build and Finder launch on macOS.
