## ADDED Requirements

### Requirement: A macOS release is signed and notarized

The macOS package built for a release tag SHALL be signed with the project's Developer ID
identity and notarized, as `macos-code-signing` describes. A tagged build whose macOS package
is not notarized SHALL count as a failed platform, so no release is created. A build started by
hand SHALL sign and notarize only when the maintainer asks for it, and otherwise SHALL produce an
ad-hoc package.

#### Scenario: A release tag is pushed

- **WHEN** a release tag builds and its macOS package is notarized
- **THEN** the draft's macOS archive is the notarized package, and its notes say so

#### Scenario: Notarization fails for a tag

- **WHEN** a release tag's macOS package is rejected by the notary service or not answered in
  time
- **THEN** no release is created, and the run names the macOS platform as failed

#### Scenario: A manual build without signing

- **WHEN** a maintainer starts a build by hand without asking for signing
- **THEN** the macOS artifact is ad-hoc signed, uses no signing secret, and its manifest says
  it is not notarized

### Requirement: Signing credentials are guarded

The signing identity and the notary key SHALL be available only to the macOS package job, and
only through a protected environment that a maintainer approves for each run. They SHALL never
be printed, written into an artifact, or left in a keychain after the job ends.

#### Scenario: A run that signs

- **WHEN** a tagged build, or a manual build that asks for signing, reaches the macOS package
  job
- **THEN** the job waits for a maintainer's approval before it can read the signing secrets,
  and removes the temporary keychain holding the identity when it ends, whether it succeeded
  or failed

#### Scenario: Other jobs

- **WHEN** the Linux, Windows, sandbox-image or release jobs run
- **THEN** none of them can read the signing identity or the notary key

## MODIFIED Requirements

### Requirement: Release builds hold no institutional credentials

A release build SHALL need no ORNL credential and no LLM inference credential. Its secrets SHALL
be read-only credentials, each scoped to one repository (the repositories its bundled
dependencies come from, and the build-inputs repository), and, for the macOS package only, the
Developer ID signing identity and the notary key that `Signing credentials are guarded`
describes.

#### Scenario: Inspecting what a release build needs

- **WHEN** the secrets available to the release workflow are listed
- **THEN** the list contains only read-only repository access and the macOS signing identity
  and notary key, with no code.ornl.gov token and no inference key

### Requirement: Release notes describe what was built

Every draft release SHALL carry generated notes that start with the one-line install command for
each platform. They SHALL then state each platform's archive with its sha256, the build-inputs
commit, and how to download, verify, install and open each platform's package by hand. On macOS
they SHALL say the package is signed and notarized, SHALL allow downloading with a browser or
`curl`, and SHALL end with opening `VISTA.app`. A maintainer adds the description of what
changed.

#### Scenario: Reading a draft's notes

- **WHEN** a maintainer opens a draft release
- **THEN** its notes start with the install commands for macOS and Linux and for Windows, pinned
  to that release's tag, and list the three archives with their sha256 values, the build-inputs
  commit, the manual install steps per platform and that the macOS package is notarized, with a
  placeholder for the changes

#### Scenario: Installing by hand on macOS

- **WHEN** a researcher follows the notes' manual macOS steps
- **THEN** they download with a browser or `curl`, check the sha256, unpack the package into
  `/Applications/VISTA` (or `~/Applications/VISTA` without administrator rights), and start
  VISTA by opening `VISTA.app`, with no step that removes macOS's quarantine mark
