## Why

The macOS package is launched through the `vista` shell script, so Finder opens a
Terminal window while first-run resources are installed and the services start. The
VISTA window appears only after that work completes. This exposes implementation logs,
makes a desktop application feel like a command-line tool, and leaves expected failures
such as an occupied port or a failed service in a terminal instead of the application.

VISTA should behave like a Mac application: launch from Finder, Spotlight or the Dock,
show honest initialization activity in a graphical window, and transition into the main
interface without opening Terminal. This change is deliberately macOS-only; Linux and
Windows retain their existing launchers, package layouts and window behaviour.

## What Changes

- Make a top-level `VISTA.app` the primary entrypoint of the macOS package while retaining
  `vista` as a diagnostic command-line entrypoint.
- Add an internal supervised mode to the Unix package launcher. On macOS the application
  invokes it to run the existing preflight, first-run setup, health checks and process-group
  cleanup without opening a second Electron process.
- Define a versioned JSON-lines startup protocol so the application displays real phases,
  completion and actionable failures without parsing human log text.
- Add a local, Mac-styled startup renderer and a narrow preload bridge. Once the UI is
  healthy, replace it with the existing sandboxed VISTA renderer, which receives no new
  host capability.
- Acquire the single-instance lock before services start and make app close or Cmd-Q stop
  the launcher and every process it owns.
- Add a production signing and notarization lane. A downloaded artifact must pass Gatekeeper
  without a terminal quarantine workaround, while preserving the sandbox runtime's
  `com.apple.security.hypervisor` entitlement.

## Capabilities

### New Capabilities

- (none)

### Modified Capabilities

- `desktop-window`: distinguish the immediate macOS startup window from the main interface,
  define graphical progress/error behaviour, and make the application own startup.
- `laptop-distribution`: make a signed and notarized `VISTA.app` the macOS package entrypoint
  without changing other platforms.

## Impact

- `electron/`: startup renderer, preload, launch state machine, macOS lifecycle and tests.
- `scripts/package_launcher.sh`: opt-in supervised protocol; default CLI behaviour remains.
- `scripts/build_local_package.sh`, manifest and macOS validation: top-level app, signing,
  notarization and Gatekeeper checks.
- `README.md`, `docs/validation-lane.md`: macOS launch and release validation.

## Non-goals

- Changing Linux or Windows startup, packaging, sandbox or window behaviour.
- Moving the startup interface into Next.js or changing the main VISTA UI.
- Running services after the last VISTA window closes.
- Auto-update, Mac App Store distribution, privileged installation, or VISTAGuard work.
