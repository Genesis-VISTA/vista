## Context

The package launcher currently owns everything: it removes quarantine, checks the host and
ports, installs missing bundled resources, imports the sandbox image, starts three services,
waits for their health endpoints, and only then starts Electron. Electron intentionally knows
nothing about those services and exits when its window closes. That division gives reliable
cleanup but necessarily exposes Terminal throughout startup.

The existing macOS Electron bundle is ad-hoc signed under `app/window/VISTA.app`. It launches
only after the shell has removed quarantine from the package. The bundled `msb` binary is also
ad-hoc signed and carries `com.apple.security.hypervisor`; the current build guards against
re-signing it because losing that entitlement prevents the code-execution sandbox from running.

The desired experience is macOS-only. Linux and Windows continue to start through their current
launchers and keep their current package layouts. The main Next.js interface also remains
unchanged, so this change does not introduce platform conditionals into the web application.

## Goals / Non-Goals

**Goals:**

- Launch the macOS package from Finder, Spotlight or the Dock without Terminal.
- Show real preflight, first-run and service readiness activity immediately.
- Present expected failures with a useful remedy, logs and retry/quit controls.
- Preserve one owner for service startup and cleanup.
- Keep the main VISTA renderer at its current browser-equivalent privilege level.
- Ship through normal macOS Gatekeeper with a reproducible signing/notarization lane.
- Make no behavioural change to Linux or Windows.

**Non-Goals:**

- Reimplementing launcher checks or service supervision in JavaScript.
- Putting startup inside Next.js, since it is not available until the last startup phase.
- Keeping VISTA running with no visible window, installing privileged helpers, auto-update,
  Mac App Store distribution, or VISTAGuard.
- Hiding an unresolved signing or sandbox-entitlement problem with user instructions to bypass
  Gatekeeper or run `xattr`.

## Decisions

### D1. macOS is app-first; other platforms are unchanged

The macOS archive exposes `VISTA.app` at its root as the documented entrypoint. The existing
`vista` file remains for diagnostics and automated package tests. Linux keeps its executable
and window-sandbox flow; Windows keeps `vista.cmd` and PowerShell. No shared default changes:
the new behaviour is selected only when the macOS app invokes the supervised launcher mode.

This is preferable to replacing every launcher with Electron because it confines risk to the
requested platform and retains already-verified platform-specific supervision.

### D2. The shell remains the service supervisor

Electron spawns `vista --supervised --progress=jsonl`. That mode performs the existing work
but does not perform `can_show_window`, start another Electron process, or print interactive
instructions. It remains alive after readiness; Electron sends it `TERM` on app quit and its
existing trap stops all service and descendant process groups.

The launcher emits a versioned JSON-lines protocol on stdout. Service output continues to go
to its current log files, and internal launcher diagnostics go to stderr/window log. Human text
is never parsed into UI state. Initial event fields are:

```json
{"protocol":1,"phase":"preflight","state":"running","label":"Checking this Mac"}
{"protocol":1,"phase":"preflight","state":"complete"}
{"protocol":1,"phase":"ui","state":"ready","url":"http://127.0.0.1:3000"}
{"protocol":1,"phase":"backend","state":"failed","code":"health-timeout","log":"backend.log"}
```

`phase` is one of `preflight`, `resources`, `sandbox`, `mcp`, `backend`, `ui`, `stopping`.
`state` is one of `pending`, `running`, `complete`, `ready`, `failed`. Labels are display text;
logic keys only on protocol, phase, state and code. The first event declares the protocol, and
an unsupported version is a fatal application error rather than a best-effort parse.

Skipped idempotent work emits `complete` with `skipped: true`, allowing subsequent starts to
advance immediately without inventing work or percentages.

### D3. Startup and the main UI use separate renderers

Electron first creates a local startup window. Its preload exposes only subscriptions to
normalized progress plus `retry`, `openLogs`, `copyDiagnostics` and `quit`. The renderer has
context isolation, sandboxing, no Node integration and no generic IPC. It uses system fonts,
light/dark appearance, reduced-motion preferences and normal macOS traffic-light controls.

When the launcher reports `ui/ready`, Electron creates the existing main window hidden at its
established 1280 × 860 default size, loads the URL, shows it on `ready-to-show`, then closes the
startup window. The compact preparation window does not determine the working window's size.
The main renderer uses the existing `webPreferences` with no preload. Separate windows
avoid carrying startup IPC into the remotely served VISTA page while making the hand-off appear
continuous.

### D4. Activities are truthful, not percentage estimates

The startup screen presents an ordered activity list and one indeterminate current activity.
Sandbox image import and backend initialization have no reliable total, so VISTA does not show
a fabricated percentage or time remaining. The first-run screen says that initial preparation
may take several minutes; later runs simply skip completed work.

Expected failures remain in the startup window with a concise message, the failed activity and
buttons for Open Logs, Retry and Quit. Copy Diagnostics includes VISTA/macOS versions, phase,
error code and log paths, but no environment values, credentials or raw request content.

Retry starts a fresh supervised launcher only after the previous child has exited and cleanup
has completed. Failures that cannot change without user action, such as an occupied port, still
allow retry after the remedy is applied.

### D5. The app owns the visible lifecycle

Electron takes the single-instance lock before spawning the launcher. A second app launch brings
the startup or main window forward and starts no second stack. This also avoids the current path
where a second command encounters occupied ports before Electron can focus the first window.

Closing the startup or main window and Cmd-Q both quit VISTA. The app sends `TERM`, shows a
Stopping state if cleanup is visible long enough, waits for the launcher's bounded graceful
cleanup, and uses the existing forced-cleanup fallback. VISTA does not remain as a menu-bar or
background application because its services and microVMs consume material resources.

Unexpected Electron termination retains the existing limitation of an untrappable hard kill.
The supervised protocol closes stdin on parent loss; the launcher adds a small EOF watcher that
requests its normal cleanup, covering ordinary crashes without introducing a privileged daemon.

### D6. The first package layout stays relocatable

The initial macOS layout is:

```text
vista-<version>-macos-<arch>/
  VISTA.app
  vista
  app/
  bin/
  node/
  payload/
  manifest.json
```

The app resolves the package root relative to its bundle and refuses with a graphical explanation
if it has been moved away from its runtime. This is less invasive than moving several gigabytes
of runtime into the bundle and preserves the artifact's current hardlinks, paths and disposable
upgrade model. A later single-bundle distribution can be proposed after this path ships.

The manifest records `entrypoint: "VISTA.app"` and the diagnostic launcher separately. Mutable
state remains outside the artifact exactly as today.

### D7. Signing and notarization are a release gate

Ad-hoc signing plus terminal-driven quarantine removal is not an acceptable production path for
the graphical entrypoint. The macOS build gains an explicit Developer ID/hardened-runtime signing
and notarization lane, staples the resulting ticket, and validates the downloaded distribution
with Gatekeeper.

The first implementation task is a signing spike against a copied completed package. It inventories
every executable Gatekeeper/notarization evaluates and proves one of two acceptable paths:

1. the notarized application may launch the existing sibling runtime without changing `msb`; or
2. `msb` and other nested executable code can be distribution-signed in a documented inside-out
   order while preserving the complete original entitlement set and passing a real sandbox spawn.

The build SHALL NOT adopt recursive `codesign --deep --force` over the package. It records `msb`'s
entitlements and CDHash before signing, verifies the intended post-signing identity/entitlements,
and runs `msb doctor` plus a sandbox smoke test. If neither path passes notarization, Gatekeeper and
the sandbox test, implementation stops at the gate; it does not add bypass instructions.

Signing credentials are release inputs, never repository files. Unsigned local development builds
remain possible, but release artifacts cannot silently skip notarization.

### D8. Development and test modes remain explicit

`./launch.sh logs` keeps its current development behaviour. Electron gets a fake-launcher fixture
for hermetic startup tests and an explicit development option for exercising the real supervised
path. The packaged smoke test may use the diagnostic launcher for service assertions, but macOS
release validation additionally launches `VISTA.app` as the user does.

## Risks / Trade-offs

- **Notarization may reject or Gatekeeper may block the sibling runtime.** The signing spike is a
  hard gate and includes a clean-machine/quarantined-artifact test.
- **Re-signing `msb` may break microVM access.** Preserve the full entitlement set and require a
  real sandbox spawn; never infer success from `codesign --verify` alone.
- **The app can be moved away from its runtime.** Detect this before starting anything and explain
  that the whole unpacked folder is the application. A single-bundle follow-up remains possible.
- **Two windows can flicker at hand-off.** Match appearance, load the normally sized main window
  hidden, and close the startup window only after the main renderer is ready.
- **A progress protocol adds launcher surface.** Version it, unit-test it, and keep default CLI
  output unchanged.
- **Close-to-quit is not the most common macOS convention.** It is retained intentionally so VISTA
  never leaves substantial services running invisibly.

## Migration Plan

Existing state needs no migration. A new package places the app at the root but uses the same
state directory and ports. Rollback is restoring the nested window plus terminal-first entrypoint;
no user data changes. Linux and Windows artifacts are byte-for-behaviour unchanged throughout.

## Open Questions

- Which Developer ID team and CI secret mechanism will sign release candidates?
- Does notarizing the top-level app plus distribution container accept the existing sibling
  runtime, or must executable runtime components be signed individually?
- Should the final artifact remain a tar archive or move to a notarized disk image after the
  app-first flow is proven? The first implementation keeps tar to minimize unrelated change.
