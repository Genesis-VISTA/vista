## Context

This change assumes `macos-signing-notarization` is implemented: the build signs every Mach-O
file through `scripts/macos/sign_package.sh`, notarizes, and staples, and the release workflow
signs in a protected environment.

The macOS package today is a folder: `VISTA.app` beside `app/`, `python/`, `node/`, `bin/`,
`payload/`, `manifest.json` and the `vista` launcher. `electron/src/package-root.js`
(`resolveMacPackage`) takes the folder holding `VISTA.app` as the package root and checks the
manifest against it. The installer puts the folder at `/Applications/VISTA/`.

The 2026-11-05 bundle spike (on Sam's Mac, outside the repo) moved the signed 0.2.4 runtime to
`VISTA.app/Contents/Resources/vista/`:

- `codesign --verify --deep --strict` passed. Only Electron's nine nested code items are
  verified as code; the ~590 runtime Mach-O files are sealed as resources, each still
  individually signed. The venvs' relative symlinks all resolved, none absolute or escaping.
- A ULMO disk image of the bundle without its payload was 1,070 MB (3.0 GB inside), took
  7 m 37 s to build, and was notarized on the first submission.
- A quarantined drag-install copy: `spctl` accepted it as Notarized Developer ID in 5.0 s; the
  bundled Python ran in 3.9 s, the venv Python with torch in 2.5 s, and `msb run` booted the
  sandbox in 0.7 s, all with no prompt. Deep verification of the full 4.5 GB bundle with its
  payload took 8.5 s.
- One Python run without `PYTHONDONTWRITEBYTECODE` wrote 2,779 `.pyc` files into the bundle,
  and verification then failed: "a sealed resource is missing or invalid (file added:
  …/__pycache__/…)". With it set, the seal stayed valid.
- Stapling failed with Error 68 because Netskope intercepts `api.apple-cloudkit.com` on ORNL's
  network. Translocation was not observed, because nothing was opened through Finder.

## Goals / Non-Goals

**Goals:**

- `VISTA.app` is the whole macOS package: moving it moves everything.
- A browser download of the disk image installs by drag-and-drop and opens offline.
- Nothing ever writes into the package, on any platform, and the build proves it.

**Non-Goals:**

- Auto-update. This layout makes it possible; it is a later change.
- Shrinking the package (separate work); the dev app `VISTA Dev.app`.

## Decisions

### D1. The runtime lives in `Contents/Resources/vista/`

The macOS staging tree becomes `VISTA.app/Contents/Resources/vista/{app,python,node,bin,
payload,manifest.json,VERSION,vista}`. The layout inside `vista/` is exactly today's package
root, so the launcher, the venvs' relative links and the smoke test's path logic work unchanged
once pointed at it.

Alternative: spread the runtime over `Contents/Frameworks` and `Contents/Helpers`, where Apple
prefers nested code. The spike showed `Resources` verifies and notarizes, and splitting the
tree would break the launcher's single-root assumption and the venvs' relative links. Rejected.

`resolveMacPackage` takes `<resourcesPath>/vista` as the package root, and the manifest's
`entrypoint` becomes the bundle (`VISTA.app`), described from inside. The "separated from its
package" check no longer applies on macOS: a bundle cannot be separated from its contents.

### D2. The payload is inside, and the whole bundle is notarized

Decided 2026-11-05. The payload goes in `Contents/Resources/vista/payload/`, sealed
with everything else, and the build notarizes the disk image containing the whole bundle. Apple
receives the corpus, weights and sandbox image at each release. They ship publicly in the
release anyway, once redistribution is cleared, which is a release precondition already. This
replaces `macos-signing-notarization` D4's payload-free submission.

Expected sizes: about 4.5 GB installed and about 1.6–1.7 GB as a ULMO disk image (the spike's
1,070 MB without the payload, plus the payload's ~600 MB compressed). That is well under the
notary's limits. If the package ever approaches 4 GB compressed, the size work comes first.

Alternatives: download the payload at first run (breaks offline installs and adds a hosted
artifact and a startup step), or put it beside the app in the disk image (lost on drag-install).
Rejected.

### D3. A stapled disk image is the release artifact

The build makes `vista-<version>-mac-arm64.dmg`, a ULMO disk image holding `VISTA.app` and an
`Applications` link. Then it signs the disk image, notarizes it once, and staples the ticket to
it. Release assets for macOS become the `.dmg` and its `.sha256`, with no `.tar.gz`.

When a stapled disk image is opened, Gatekeeper records its ticket, which covers the app's code
inside, so an app dragged out of it should open offline. That is not yet proven (the spike could
not staple). Task 6.2 tests it on a GitHub runner's artifact. If an app copied out of the disk
image needs its own ticket, the build also staples the app before making the disk image, which
means a second notarization submission per release. That is acceptable: the limit is about 75 a
day.

### D4. The package never writes into itself, on every platform

- **Build:** environments are created with bytecode compiled (`uv sync --compile-bytecode`),
  and the bundled interpreter's standard library is compiled too.
- **Run:** both launchers export `PYTHONDONTWRITEBYTECODE=1`, so a module missing its bytecode
  is compiled in memory, never written. `PYTHONPYCACHEPREFIX` pointing into `~/.vista` would
  also work, but it adds a cache to manage for no measured gain.
- **UI:** the Next.js standalone server writes its image-optimization cache under its own
  `.next/cache`. Either images are served unoptimized (`images.unoptimized`, since they are
  local and few), or the cache is moved into `~/.vista`. Read the Next.js docs in
  `ui/node_modules/next/dist/docs/` to choose. The verification below finds any other writer.
- **Verification:** the smoke test records every file's size and mtime in the unpacked package
  before starting, runs its session, and checks nothing changed. On macOS it also runs strict
  deep verification of the bundle afterwards. This is laptop-distribution's "A session leaves
  the artifact unchanged" scenario, on all three platforms.

The Windows launcher rewrites `pyvenv.cfg` at start (its `home` line). That is a write into the
package, which this rule forbids. It moves to build time, and where the absolute path cannot be
known at build time (Windows has no relative `home`), the launcher passes the interpreter
location in the environment instead. Task 3.3 settles which works.

### D5. Refuse to run from a read-only or translocated location

Decided 2026-11-05: explain and stop. At startup on macOS, the Electron main process
treats two cases as not installed: the app's path is under `/private/var/folders/…/AppTranslocation/`,
or the volume it is on is read-only (a mounted disk image). In either case the startup window
shows the drag-to-Applications instruction, and it starts nothing, before any launcher runs. Any
other location works, including a writable folder outside Applications, so the smoke test and
developers can run the app from wherever they unpack it.

Alternative: Electron's `app.isInApplicationsFolder()`. It would refuse a working copy in, say,
`~/Desktop`, which the "Relocatable and transferable" requirement allows. Rejected.

### D6. The installer installs the app, from the disk image

`install.sh` on macOS downloads the `.dmg` and its `.sha256` with curl (no quarantine), checks
the hash, attaches the image read-only and hidden, copies `VISTA.app` with `ditto` to a
temporary name beside the destination, renames it into place, and detaches. The destination is
`/Applications/VISTA.app`, falling back to `~/Applications/VISTA.app` by the same rule as today.
After success it removes a `VISTA/` folder it created under the old layout (identified by its
manifest), re-points `~/.local/bin/vista` at
`VISTA.app/Contents/Resources/vista/vista`, and registers the app with `lsregister`.

The "refuse while running" check now looks for processes started from inside the bundle.
Whether macOS App Management prompts when Terminal replaces a signed app is settled by
`macos-signing-notarization` task 6.1, and its answer carries over here.

## Risks / Trade-offs

- [An app dragged out of a stapled disk image may still need the network on first launch] →
  Task 6.2 tests it offline. Fallback in D3: staple the app too.
- [A first launch verifies about 100,000 files] → The spike measured 5 s for Gatekeeper's
  assessment and 8.5 s for a full seal check. If a real `open` shows a long "Verifying…" bar,
  record it in the release notes. The size work reduces it.
- [Something else writes into the package that the spike did not exercise] → D4's
  unchanged-package check runs on every build, on all platforms, after a session that uses the
  sandbox and retrieval.
- [Existing installs move from `/Applications/VISTA/` to `/Applications/VISTA.app`, and Dock
  icons pinned to the old app point at a removed path] → The installer prints that the Dock
  icon needs pinning again. State in `~/.vista` is untouched.
- [The DMG build adds about 8 minutes to the macOS job] → Acceptable; the job is shorter than
  the Windows one.

## Migration Plan

1. Land behind the existing release flow: the macOS job produces the `.dmg` instead of the
   `.tar.gz`, and the installer handles both the old and the new layout.
2. Test a manual signed build end to end: browser download, drag-install, offline first launch,
   the one-line installer upgrading from the last folder-layout release.
3. Release. Rollback: the previous release's installer still installs the folder layout; a
   researcher on the new layout who reinstalls an older version gets the old folder beside the
   app, and the next newer install cleans it up.
