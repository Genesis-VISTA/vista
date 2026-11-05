Implement after `macos-signing-notarization` is archived: its `sign_package.sh`, entitlements
and CI signing are assumed throughout.

## 1. The package never writes into itself

- [ ] 1.1 Add the unchanged-package check to `smoke_test_package.sh`: record every file's
  size and mtime before the session and compare after; on macOS also run strict deep
  verification afterwards. Run it on the current package and record what it finds (expected:
  the `.pyc` files, maybe the UI's `.next/cache`). This is the group's failing test.
- [ ] 1.2 Compile bytecode at build time for every environment and the bundled standard library
  (design D4). Verify that the package has `.pyc` for every `.py` it imports in a session.
- [ ] 1.3 Export `PYTHONDONTWRITEBYTECODE=1` in `package_launcher.sh` and
  `package_launcher.ps1`. Verify with the supervised launcher tests and 1.1's check on macOS.
- [ ] 1.4 Stop the UI server writing under its own `.next/` (read the Next.js docs on
  `images` first, design D4). Verify by loading a page with an image and running 1.1.
- [ ] 1.5 Move the Windows launcher's `pyvenv.cfg` rewrite out of run time (design D4), and
  verify on Windows with `scripts/tests/package_launcher_supervised_test.ps1` and 1.1 in the
  Windows package job.

## 2. Bundle layout

- [ ] 2.1 Stage the macOS package as `VISTA.app/Contents/Resources/vista/…` (design D1), with
  the manifest describing the bundle. Verify the manifest fields and the tree with `--keep-staging`.
- [ ] 2.2 Make `sign_package.sh` work on the bundle: runtime files signed first, Electron parts
  next, the outer app last. Verify that 599 files sign and the bundle passes strict deep
  verification.
- [ ] 2.3 Update `resolveMacPackage` in `electron/src/package-root.js` to take
  `<resourcesPath>/vista` as the package root, and its unit tests. Verify with
  `cd electron && npm test`.
- [ ] 2.4 Point the smoke test at the bundle's runtime and run it end to end on a signed local
  build, sandbox included. Verify that every check passes, 1.1 included.

## 3. Not-installed check

- [ ] 3.1 In the Electron main process, before any launcher starts, detect translocation and a
  read-only volume (design D5) and show the startup window's not-installed message. Add its
  text to `startup-protocol.js`. Verify with unit tests that fake each path, and that a
  writable non-Applications path is allowed.
- [ ] 3.2 Add an e2e case to `npm run test:e2e` that opens the app from a read-only disk image
  and expects the message and no launcher process. Verify it passes on macOS.

## 4. Disk image and notarization

- [ ] 4.1 Build `vista-<version>-mac-arm64.dmg` (ULMO, `VISTA.app` plus an `Applications`
  link), sign it, notarize it with the payload included, and staple it (design D2, D3); record
  size and time in the build log. Verify with `stapler validate`, `spctl -a -t open
  --context context:primary-signature`, and the notary log.
- [ ] 4.2 Have the smoke test take the `.dmg`: attach it read-only, copy the app out, and run
  from the copy. Verify a full local build passes.
- [ ] 4.3 Change `.github/scripts/package.sh`, `release.yml` and the release job to publish
  the `.dmg` and its `.sha256` for macOS. Verify with a manual signed run that the draft carries
  the `.dmg`.

## 5. Installer and docs

- [ ] 5.1 Make `scripts/install.sh` install from the disk image into `/Applications/VISTA.app`
  or `~/Applications/VISTA.app`, remove the old `VISTA/` folder it created, re-point
  `~/.local/bin/vista`, and print the Dock note (design D6). Extend `scripts/test_install.sh`
  with a fake disk image for a fresh install, an upgrade from the folder layout, an install
  without admin rights, and refusal while running. Verify with `./scripts/ci-local.sh install`.
- [ ] 5.2 Update `.github/release-notes.md` (drag-install steps), `docs/installing.md`,
  `docs/releasing.md` (the real-Mac check now starts from the `.dmg`),
  `docs/building-packages.md`, `README.md` and `AGENTS.md` (the macOS layout paragraph). Verify
  with `./scripts/ci-local.sh docs` and a grep for `/Applications/VISTA/` and "package root".

## 6. End-to-end on a real Mac

- [ ] 6.1 From a manual signed CI build: download the `.dmg` with a browser, open it, drag
  VISTA to Applications, open it, and run a chat that uses the sandbox and retrieval. Verify
  there are no prompts, and that the bundle still passes strict deep verification afterwards.
- [ ] 6.2 Repeat 6.1's first launch with networking off, on a Mac that has never seen this
  build. If Gatekeeper needs the network, apply design D3's fallback and repeat.
- [ ] 6.3 Open VISTA straight from the mounted disk image and from Downloads without moving it.
  Verify both show the not-installed message and start nothing.
- [ ] 6.4 Upgrade an install of the last folder-layout release with the new one-line
  installer. Verify that `/Applications/VISTA/` is gone, `/Applications/VISTA.app` opens, chats
  and settings are kept, and `vista` in a terminal still runs the diagnostic launcher.
