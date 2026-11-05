## Context

Today `build_local_package.sh` makes exactly one `codesign` call: it re-signs `VISTA.app` ad hoc
after renaming it (`stage_window_macos`), and its preflight refuses any other call, because
re-signing `msb` would strip its hypervisor entitlement. Everything else ships with whatever
signature its wheel or download carried: in the 0.2.4 package, 599 Mach-O files, 36 of them
with ad-hoc signatures that no longer verify (`desktop-app-startup/signing-spike.md`).

The 2026-11-05 spike (on Sam's Mac, outside the repo) settled the questions that spike left
open. With Feiyi Wang's Developer ID Application identity (team `BG79PHL85W`):

- All 599 files signed with hardened runtime and timestamp in 21 s, 8 at a time, with no
  failures, and all of them verify strictly.
- The package without `payload/`, zipped with `ditto`, is 945 MB. The notary service accepted
  it on the first submission; its only warnings were 20 joblib test-data `.gz` files it could
  not unpack. The ticket lists 649 code hashes.
- A fresh copy with every file quarantined ran `uv`, `node`, `msb` and Python (importing
  torch, sentence-transformers, grpc, chromadb, PyMuPDF and the rest, with a ctypes callback)
  with no prompt. `spctl` assessed `VISTA.app`, `uv` and `node` as Notarized Developer ID.
- The package smoke test passed every check, and `msb run` booted the sandbox image. A copy of
  `msb` signed with only `com.apple.security.hypervisor` booted it too.
- Stapling failed on ORNL's network: Netskope re-signs TLS to `api.apple-cloudkit.com` with
  ORNL's CA, and `stapler` rejects it (Error 68). Gatekeeper's own online lookup was unaffected.
- A signed but not yet notarized file that is quarantined gets a "could not verify" prompt, and
  dismissing it kills the process and records the refusal for that file.

## Goals / Non-Goals

**Goals:**

- A release's macOS package is Developer-ID-signed, notarized and stapled by CI, with no
  manual step.
- A local build signs the same way when given an identity, and behaves as today without one.

**Non-Goals:**

- Changing the package layout. The single `VISTA.app` bundle and a DMG are the next change; this
  one must not make that harder.
- Signing anything for Linux or Windows.

## Decisions

### D1. The build owns signing; CI only supplies credentials

Signing lives in `build_local_package.sh`, as a step between `report_path_lengths` and
`create_archive`, so a local build and a CI build sign identically. `write_manifest` moves after
it so the manifest can record the outcome. The build reads three settings, from the environment
or the repo-root `.env` like its other credentials:

- `VISTA_MACOS_SIGNING_IDENTITY`: a codesigning identity (SHA-1 or name);
- `VISTA_MACOS_KEYCHAIN`: optional, the keychain holding it (CI's temporary one);
- notary credentials: either `VISTA_MACOS_NOTARY_PROFILE` (a `notarytool` keychain profile,
  for a developer's Mac) or `VISTA_MACOS_NOTARY_KEY`, `_KEY_ID` and `_ISSUER` (a `.p8` path and
  its IDs, for CI).

Unset identity → today's ad-hoc path. Set identity → the preflight checks it is valid
(`security find-identity -v`), unexpired, and has notary credentials, and fails otherwise
(spec: "A build without an identity stays ad-hoc and says so"). It also warns when the
certificate expires within 60 days: the current one expires on 2027-12-05.

Alternative: sign in CI after the build, on the archive. That would leave local builds unable
to reproduce a release and repeat the unpack/repack. Rejected.

The signing and notarizing steps themselves live in `scripts/macos/sign_package.sh <package-dir>`,
which the build calls. It can also run on any unpacked package, as the spike did, and its
inventory and ordering can be tested against a fake package of ad-hoc-signed stub binaries
without an identity.

`electron/scripts/package.js` keeps not signing (`osxSign` unset): `@electron/osx-sign` knows
only the app, not its 586 siblings.

### D2. Sign by inventory, inside-out, in parallel

The build finds Mach-O files by their magic bytes, not by extension or exec bit: the spike's
scan found exactly the 599 that `file` found, without spawning `file` 100,000 times. Order:

1. every Mach-O outside `VISTA.app`, in parallel (8 at a time). They are loose files with no
   nesting, so order among them does not matter;
2. inside `VISTA.app`: its loose dylibs and helpers (`chrome_crashpad_handler`, `ShipIt`), then
   each `.framework`, then the four helper apps, then the app itself.

`codesign --deep` is not used to sign: Apple discourages it, and it would apply one set of
entitlements to everything. It is used to verify.

### D3. Entitlements by role, in files beside the build

Four plists under `scripts/macos/entitlements/`, matching the spec's allowed sets:

| File | Used by |
|---|---|
| `msb.plist` | `com.apple.security.hypervisor` |
| `jit.plist` | `node`, `VISTA.app`, and the Renderer, GPU and plain helpers |
| `electron-plugin.plist` | the Plugin helper: Electron's default plugin set |
| (none) | everything else, Python included |

`msb` drops `disable-library-validation`: it needed it only to load an ad-hoc `libkrunfw`,
which is now signed by the same team. The ad-hoc path keeps today's behaviour of not touching
`msb`.

Alternative: Electron's full `default.darwin.plist` for the app, which adds camera, microphone,
location and so on. VISTA uses none of them, and they would show as requests in privacy
prompts. Rejected.

### D4. Notarize the code, not the payload, then archive

After signing, the build zips the staging tree without `payload/` with `ditto -c -k
--keepParent` (Apple's notary takes zip, not tar), runs `notarytool submit --wait --timeout`,
saves the log beside the build log, and fails on anything but Accepted, printing the log's
issues. Then `stapler staple VISTA.app`, `stapler validate`, a final deep verification, and
only then `create_archive`.

Leaving the payload out keeps the corpus, embedding weights and sandbox image (none of them
Mach-O) from leaving the project, and cuts the upload from about 1.7 GB to under 1 GB. The
ticket covers code hashes, so files added afterwards do not affect it.

A stapled ticket lets `VISTA.app` pass Gatekeeper offline. The loose binaries beside it cannot
hold a ticket (only bundles, disk images and installer packages can), so a quarantined copy's
first run of them needs the network for Gatekeeper's lookup. That is acceptable for this
layout. The bundle change removes it.

The smoke test runs after `create_archive`, as now, on the unpacked archive. It also asserts
the signing facts: strict verification of every Mach-O, the entitlement sets, and
`spctl -a -t exec` accepting `VISTA.app`.

### D5. CI: a protected environment and a temporary keychain

The `package (mac-arm64)` job runs in a GitHub environment, `macos-signing`, whose secrets are
`MACOS_SIGNING_P12` (base64), `MACOS_SIGNING_P12_PASSWORD`, `MACOS_NOTARY_KEY` (the `.p8`),
`MACOS_NOTARY_KEY_ID` and `MACOS_NOTARY_ISSUER`. It requires a maintainer's approval for every
run. Other jobs have no environment, so they cannot read them. Tag builds always sign. A manual
build signs when its `sign` input is true; otherwise it does not enter the environment and stays
ad-hoc.

The job creates a keychain with a random password, imports the `.p12`, and authorizes
`codesign` on it with `security set-key-partition-list`. That is the scripted form of the
"Always Allow" a developer clicks once, and without it every `codesign` waits on a dialog (the
spike's first run). It adds the keychain to the search list. It writes the `.p8` to a file
outside the workspace. An `always()` step deletes the keychain and the key file. This lives in
`.github/scripts/package.sh`, so the job's steps still run the same locally.

Alternative: sign on a maintainer's Mac after CI, from the draft's asset. That keeps the key on
one machine, but makes every release a manual chore, and stapling fails on ORNL's network.
Rejected, since the team owner is also the organization owner and agreed to the secrets.

### D6. The launcher keeps its quarantine removal only for ad-hoc packages

`package_launcher.sh` removes `com.apple.quarantine` from the whole package. For a notarized
package that is unnecessary, and it masks the very thing release verification should prove.
It now does so only when the manifest says the package is not notarized.

## Risks / Trade-offs

- [Notarization is slow or stalls on Apple's side, which happens for hours at a time] → The
  timeout fails the job cleanly. Rerunning the macOS job resubmits the same tree.
- [Stapling on a GitHub runner is untested: the spike's attempts were all on ORNL's network] →
  The first CI run (task 5.3) proves it. If it fails, the build reports it and the release
  waits, since loose binaries rely on the online lookup anyway.
- [Upgrading a Developer-ID-signed app from Terminal triggers macOS App Management, which the
  ad-hoc app never did] → Task 6.1 retests the one-line installer's upgrade on a real Mac. If it
  prompts, the installer explains the prompt before replacing the app.
- [The identity is Feiyi Wang's personal team, so his name is the publisher, and the
  certificate leaves with him] → Accepted for now. Moving to an organisational team later
  changes the team ID once: Keychain items and privacy grants reset for that upgrade.
- [A leaked `.p12` lets anyone sign as the team, and only Apple can revoke it] → Environment
  approval, secrets never printed, keychain deleted after each run. Feiyi can revoke the `.p8`
  at once.
- [Hardened runtime breaks a binary the spike did not exercise] → The spike ran the full smoke
  test, the sandbox and the native imports. The smoke test now runs on every signed build.

## Migration Plan

1. Land signing with the identity unset: builds are unchanged.
2. Add the environment and secrets, run a manual build with `sign: true`, check that stapling
   works on the runner, and run the real-Mac smoke test against it.
3. The next tag is the first notarized release. Rollback: remove the environment's secrets and
   revert the workflow commit. Builds fall back to ad-hoc, and the curl installer keeps working
   as before.
