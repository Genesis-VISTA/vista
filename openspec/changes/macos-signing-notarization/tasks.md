The 2026-11-05 spike (design, Context) is the reference for every step below: its scratch
scripts signed, notarized and verified the 0.2.4 package by hand.

## 1. Signing script

- [ ] 1.1 Add the four entitlement plists under `scripts/macos/entitlements/` (design D3), and
  check each with `plutil -lint`.
- [ ] 1.2 Add `scripts/macos/sign_package.sh <package-dir>`: find Mach-O files by magic bytes,
  sign them inside-out in parallel with hardened runtime, timestamp and the role's entitlements
  (design D2), then verify every file strictly and `VISTA.app` deeply. Fail naming any file
  that does not sign or verify. Verify by running it on an unpacked 0.2.4 package with an
  identity: 599 files signed, all verify.
- [ ] 1.3 Add `scripts/tests/sign_package_test.sh`: a fake package of ad-hoc-signed stub
  binaries (an app bundle with a framework and helpers, loose dylibs, an `msb` and a `node`)
  signed with an ad-hoc identity, checking the inventory, the signing order and each file's
  entitlements. Run it from `./scripts/ci-local.sh` on macOS hosts (skipped elsewhere, saying so), and
  verify it fails when one stub is made unsignable.
- [ ] 1.4 Add notarization to the script: zip without `payload/` with `ditto`, `notarytool
  submit --wait --timeout`, save the log, fail on anything but Accepted and print the issues,
  then staple and validate `VISTA.app`. Accept either a keychain profile or a `.p8` with its
  IDs (design D1). Verify by notarizing the 0.2.4 package from a non-ORNL network, or let 5.3
  cover stapling.

## 2. Build integration

- [ ] 2.1 In `build_local_package.sh`, read `VISTA_MACOS_SIGNING_IDENTITY`, `VISTA_MACOS_KEYCHAIN`
  and the notary settings. With an identity, the preflight checks it is valid and unexpired,
  that notary credentials are present, and warns within 60 days of expiry; without one it
  logs that the package will be ad-hoc. Replace the "only one `codesign --sign`" preflight check
  with a check that signing happens only in `sign_package.sh` and the ad-hoc window step.
  Verify with `--check` in three cases: no identity, a valid one, and a name not in the
  keychain.
- [ ] 2.2 Call `sign_package.sh` between `report_path_lengths` and `create_archive` when an
  identity is set, and move `write_manifest` after it. Record `signing` in the manifest:
  `ad-hoc`, or the Developer ID team with `notarized` and `stapled` (spec: "The manifest
  records how the package was signed"). Verify with a signed local build and an unsigned one,
  and read both manifests.
- [ ] 2.3 Extend `smoke_test_package.sh` for macOS packages whose manifest says notarized:
  every Mach-O verifies strictly, entitlements match design D3 exactly, and `spctl -a -t exec`
  accepts `VISTA.app`. Ad-hoc packages skip these checks and say so. Verify against the signed
  and the unsigned builds from 2.2.
- [ ] 2.4 In `package_launcher.sh`, remove the quarantine mark only when the manifest says the
  package is not notarized (design D6). Verify with
  `scripts/tests/package_launcher_supervised_test.sh` and by starting a quarantined notarized
  package, which must start without the removal.

## 3. CI

- [ ] 3.1 In `.github/scripts/package.sh`, when the signing secrets are present: create a
  temporary keychain with a random password, import the `.p12`, run `set-key-partition-list`
  for `codesign`, add it to the search list, write the `.p8` outside the workspace, and export
  the build's signing settings. Delete the keychain and key file on exit, including on failure
  (design D5). Verify locally by running `package.sh` with a `.p12` exported from a developer's
  keychain, then checking no keychain or key file is left.
- [ ] 3.2 In `release.yml`, run the mac-arm64 package job in the `macos-signing` environment for
  tags, and for manual runs whose new `sign` input is true; other jobs and unsigned manual runs
  do not use it. Verify with the workflow linting that GitLab CI runs, and by reading the
  rendered plan for a tag and for a manual run.
- [ ] 3.3 Document in `docs/releasing.md` how to create the `macos-signing` environment, its
  required reviewers and its five secrets, how to export the `.p12` and base64 it, how to rotate
  the certificate (expires 2027-12-05) and the API key, and how to revoke both. Verify with
  `./scripts/ci-local.sh docs`.

## 4. Release notes and docs

- [ ] 4.1 Update `.github/release-notes.md`: the macOS steps say the package is notarized,
  allow a browser download, and drop the `xattr`/System Settings workaround (spec: "Release
  notes describe what was built"). Verify with `render-notes.sh` output for a test version.
- [ ] 4.2 Update `docs/installing.md` and `docs/building-packages.md`: browser downloads work
  for notarized packages; keep the quarantine note only for ad-hoc local builds; document the
  build's signing settings. Verify with `./scripts/ci-local.sh docs`.
- [ ] 4.3 Update every remaining mention of the macOS package being ad-hoc signed or needing
  `curl` (`AGENTS.md`, `README.md`, `docs/`). Verify by grepping them for "ad hoc", "ad-hoc",
  "quarantine" and "curl, not a browser" and checking each mention is still true.

## 5. First signed builds

- [ ] 5.1 Create the `macos-signing` environment on `Genesis-VISTA/vista`, with Feiyi Wang and
  Sam Baumann as required reviewers, and add the five secrets. Verify that the environment's
  settings page lists them, and that no other job can read them.
- [ ] 5.2 Run a manual build with `sign: true` on the branch. Verify the job waits for approval,
  notarization is Accepted, and the archive's manifest says notarized.
- [ ] 5.3 Verify stapling worked on the runner (`stapler validate` in the job log). If it
  failed, record why and decide before tagging.
- [ ] 5.4 Download the run's macOS artifact with a browser on a real Mac, unpack it with Finder,
  open `VISTA.app` without any `xattr` step, and run the full smoke test, sandbox included,
  against it. Record the results here.

## 6. Installer upgrade

- [ ] 6.1 Install the last ad-hoc release with the one-line installer, then upgrade it to the
  signed build from 5.2 the same way. Record whether macOS App Management prompts, in both
  `/Applications/VISTA` and `~/Applications/VISTA`.
- [ ] 6.2 If it prompts, make the installer say beforehand what the prompt is and what to
  click, and extend `scripts/test_install.sh` for the new message. Verify by repeating 6.1.
