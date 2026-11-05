## Why

The macOS package ships ad-hoc signed, so it opens only because the one-line installer
downloads with `curl`, which macOS does not quarantine. A browser download is blocked, the
release notes carry an `xattr` workaround, and a managed Mac that refuses ad-hoc code cannot run
VISTA at all. `desktop-app-startup` dropped Developer ID signing for want of a certificate
(its design D7 and `signing-spike.md`). We now have one: a Developer ID Application identity
on Feiyi Wang's team (`BG79PHL85W`), with a notary API key. A spike on 2026-11-05 signed every
Mach-O file of the 0.2.4 package, notarized it on the first submission, and ran it from a
quarantined copy with no Gatekeeper prompt and every smoke check passing, sandbox included.

## What Changes

- Release builds of the macOS package sign every Mach-O file with the Developer ID identity,
  with the hardened runtime and a secure timestamp, and only the entitlements each binary
  needs: `msb` the hypervisor, `node` and the window JIT, everything else none.
- `msb` loses `disable-library-validation`: once its `libkrunfw` carries the same team's
  signature, it needs only `com.apple.security.hypervisor`.
- The package, without `payload/`, is submitted to Apple's notary service, the build waits for
  the verdict and fails on anything but Accepted, and the ticket is stapled to `VISTA.app`.
  The payload holds no code and is never uploaded.
- Signing happens in `build_local_package.sh`, so a local build signs exactly as CI does when
  given the identity. Without one it stays ad-hoc signed, as today, and says so in its output
  and manifest.
- The release workflow's macOS job gets the identity and the notary key from secrets on a
  GitHub environment, imports the identity into a temporary keychain, and deletes it at the
  end of the job.
- The release notes, `docs/installing.md` and the launcher drop the curl-only advice and the
  quarantine workaround for notarized packages. Curl installs keep working.
- Out of scope: the single self-contained `VISTA.app` and a DMG (a follow-up change), package
  size, Windows Authenticode, Linux, and auto-update.

## Capabilities

### New Capabilities

- `macos-code-signing`: how the macOS package is signed and notarized, which entitlements each
  binary may carry, what a build without an identity does, and how signing is verified.

### Modified Capabilities

- `release-builds`: release builds may hold the signing identity and notary key, gated by an
  environment, besides read-only repository credentials; a macOS release requires a notarized
  package; the macOS release notes stop telling researchers to avoid the browser and drop the
  quarantine workaround.

## Impact

- `scripts/build_local_package.sh`: signing replaces the single ad-hoc `codesign` and the
  preflight that refuses any other; notarization and stapling after the archive's tree is
  final; signing status in `manifest.json`.
- New entitlement files beside the build (one per role), and `electron/scripts/package.js`
  unchanged: the build keeps owning macOS signing.
- `.github/workflows/release.yml` and `.github/scripts/package.sh`: environment, secrets,
  temporary keychain.
- `scripts/package_launcher.sh`: the quarantine removal stays only for ad-hoc packages.
- `.github/release-notes.md`, `docs/installing.md`, `docs/releasing.md`,
  `docs/building-packages.md`.
- The one-line macOS installer: upgrading a Developer-ID-signed app from Terminal may now meet
  macOS App Management, which the ad-hoc app never did (desktop-app-startup D7); it must be
  retested and, if needed, handled.
- Notarization adds minutes, occasionally hours, to the macOS release job, and uses Apple's
  limit of about 75 submissions a day on that team.
