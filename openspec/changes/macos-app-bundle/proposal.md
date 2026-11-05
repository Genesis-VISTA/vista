## Why

After `macos-signing-notarization`, the macOS package is notarized but still a folder:
`VISTA.app` beside a 4 GB runtime it must stay next to, installed as `/Applications/VISTA/`.
A researcher cannot do the ordinary Mac thing (download a disk image, drag VISTA to
Applications), and the loose binaries cannot hold a stapled ticket, so a quarantined copy's
first run needs the network. `desktop-app-startup` listed "a single self-contained app bundle"
as out of scope only for want of a certificate. A spike on 2026-11-05 put the signed 0.2.4
runtime under `VISTA.app/Contents/Resources/vista/`. The bundle verified strictly, a DMG of it
was notarized on the first submission, and a quarantined drag-install ran Python with torch and
booted the sandbox with no prompt, Gatekeeper's check taking 5 s. It also found that the
runtime writes `.pyc` files into the package, which breaks a sealed bundle after its first run.

## What Changes

- **BREAKING (layout):** the macOS package becomes one self-contained `VISTA.app`, with the
  runtime, the payload, the manifest and the diagnostic launcher under
  `Contents/Resources/vista/`. Linux and Windows are unchanged.
- **BREAKING (release asset):** a macOS release ships `vista-<version>-mac-arm64.dmg`, a signed,
  notarized and stapled disk image holding `VISTA.app` and a link to Applications, instead of
  the `.tar.gz` archive.
- The whole bundle, payload included, is sealed and notarized. Apple therefore receives the
  corpus, weights and sandbox image with every release, which the public release ships anyway.
  This replaces `macos-signing-notarization`'s rule of leaving the payload out.
- The package never writes into itself at run time on any platform: bytecode is compiled at
  build time, and the launchers stop Python writing it. The build's verification checks that
  a session leaves the package unchanged.
- Opened from the disk image or from a translocated location, VISTA explains that it must be
  dragged to Applications first, and starts nothing.
- The one-line installer installs `VISTA.app` into `/Applications` (or `~/Applications`) from
  the disk image, removes the old `/Applications/VISTA/` folder layout after a successful
  install, and keeps `~/.local/bin/vista` pointing at the diagnostic launcher.
- Out of scope: auto-update, Linux and Windows packaging, the package-size reductions, the
  development app (`build_mac_dev_app.sh`).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `laptop-distribution`: the macOS entry point is a self-contained `VISTA.app`; the package is
  never written to at run time, and verification checks that.
- `macos-code-signing` (from `macos-signing-notarization`): the notarized unit is the whole
  bundle with its payload, delivered in a stapled disk image; a browser-downloaded disk image
  opens with a drag-install.
- `release-builds`: the macOS release asset is a disk image; the installer installs
  `VISTA.app` itself into Applications; the release notes describe a drag-install.
- `desktop-window`: VISTA opened from a read-only or translocated location explains how to
  install it and starts nothing.

## Impact

- Depends on `macos-signing-notarization` being implemented and archived first: its specs are
  the ones modified here.
- `scripts/build_local_package.sh` (macOS staging layout, bytecode compilation, DMG creation),
  `scripts/macos/sign_package.sh` (bundle-relative paths, notarizing the DMG),
  `scripts/smoke_test_package.sh` (unchanged-package check, DMG unpacking).
- `scripts/package_launcher.sh` and `package_launcher.ps1` (no bytecode writes), and the
  launcher's package-root and manifest handling on macOS.
- `electron/src/package-root.js` and `main.js` (finding the runtime inside the bundle, the
  read-only-location check), `electron/src/startup-protocol.js` (its message).
- `scripts/install.sh` and `scripts/test_install.sh` (disk-image install, old-layout cleanup),
  `.github/scripts/package.sh`, `render-installers.sh`, `render-notes.sh`,
  `.github/release-notes.md`.
- `docs/installing.md`, `docs/releasing.md`, `docs/building-packages.md`, `README.md`,
  `AGENTS.md` (which describes "`VISTA.app` at the package root").
- Existing installs: the installer's next upgrade moves them from `/Applications/VISTA/` to
  `/Applications/VISTA.app`. State in `~/.vista` is untouched.
