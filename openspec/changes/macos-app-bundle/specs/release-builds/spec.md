## MODIFIED Requirements

### Requirement: A version tag builds every release platform

Pushing a tag of the form `v<semver>` SHALL build the linux-x86_64, mac-arm64 and
windows-x86_64 packages and SHALL create one draft GitHub release for that tag. The draft
SHALL carry each platform's artifact and its `.sha256` file: an archive for Linux and Windows,
and a disk image for macOS. No release SHALL be created unless all three platforms built and
passed their verification. A private rehearsal repository MAY narrow the set of platforms it
builds, but a public repository SHALL NOT create a release from fewer than all three.

#### Scenario: A release tag is pushed

- **WHEN** the tag `v0.2.0` reaches the GitHub repository
- **THEN** a draft release named for `v0.2.0` is created with the `vista-0.2.0-linux-x86` and
  `vista-0.2.0-win-x86` archives and the `vista-0.2.0-mac-arm64.dmg` disk image, each with a
  matching `.sha256` file

#### Scenario: One platform fails

- **WHEN** a release tag is pushed and any one platform's build or verification fails
- **THEN** no release is created for that tag, and the failing platform is named in the run's
  result

#### Scenario: A narrowed platform set on a public repository

- **WHEN** a release tag is pushed to a public repository whose platform set has been narrowed
- **THEN** no release is created, and the run says the platform set must be left unset there

#### Scenario: A prerelease tag is pushed

- **WHEN** a tag with a prerelease suffix, such as `v0.2.0-rc1`, is pushed
- **THEN** the draft release is marked as a prerelease

### Requirement: Release notes describe what was built

Every draft release SHALL carry generated notes that start with the one-line install command for
each platform. They SHALL then state each platform's artifact with its sha256, the build-inputs
commit, and how to download, verify, install and open each platform's package by hand. On macOS
they SHALL say the package is signed and notarized, and SHALL describe downloading the disk
image, opening it, and dragging VISTA to Applications. A maintainer adds the description of what
changed.

#### Scenario: Reading a draft's notes

- **WHEN** a maintainer opens a draft release
- **THEN** its notes start with the install commands for macOS and Linux and for Windows, pinned
  to that release's tag, and list the three artifacts with their sha256 values, the
  build-inputs commit, the manual install steps per platform and that the macOS package is
  notarized, with a placeholder for the changes

#### Scenario: Installing by hand on macOS

- **WHEN** a researcher follows the notes' manual macOS steps
- **THEN** they download the disk image with a browser or `curl`, optionally check its sha256,
  open it, drag VISTA to Applications, and open VISTA there, with no step that removes macOS's
  quarantine mark

### Requirement: A release installs with one command

Every release SHALL carry an installer for macOS and Linux and one for Windows, each with that
release's version written in, so one command downloads, verifies, installs and starts that
release's package for the machine it runs on. An installer SHALL install into one fixed place per
platform, whatever the version: on macOS the application `/Applications/VISTA.app`, or
`~/Applications/VISTA.app` when the installer cannot write to `/Applications`;
`~/.local/share/vista/app` on Linux; and `%LOCALAPPDATA%\VISTA\app` on Windows. It SHALL make
VISTA launchable from that platform's usual place, with VISTA's icon.

An installer SHALL NOT install from an artifact that does not match its `.sha256`. It SHALL leave
a previously installed version in place until the new one is, SHALL refuse to replace a version
that is running, and SHALL NOT touch VISTA's state directory. After a successful install it SHALL
remove packages left by earlier install layouts, including on macOS a `VISTA` folder in
`/Applications` or `~/Applications` that an earlier installer created, and the researcher's own
`~/Applications/VISTA.app` once VISTA is installed in `/Applications`. It SHALL NOT remove
anything in `/Applications` it did not install. Run again for a version already installed, it
SHALL start that copy without downloading. When it starts VISTA, it SHALL start the application
entry point rather than the diagnostic launcher.

#### Scenario: Installing the newest release

- **WHEN** a researcher on a supported machine runs the install command from the README
- **THEN** the newest published release's package for that machine is downloaded, checked,
  installed into its platform's fixed place, and VISTA's application opens

#### Scenario: Launchable after installing

- **WHEN** an install completes
- **THEN** VISTA is in Spotlight and the Apps view (Launchpad) on macOS, in the app menu on Linux,
  and in the Start menu on Windows, each with VISTA's icon

#### Scenario: Visible in Finder's Applications on macOS

- **WHEN** an administrator installs VISTA on a Mac
- **THEN** it is installed as `/Applications/VISTA.app`, so Finder's Applications shows VISTA
  directly, without `sudo`

#### Scenario: Installing on a Mac without administrator rights

- **WHEN** the installer cannot write to `/Applications`, or to an existing
  `/Applications/VISTA.app` that another account installed
- **THEN** it installs `~/Applications/VISTA.app`, says why, and leaves
  `/Applications/VISTA.app` untouched; VISTA is still in Spotlight and the Apps view

#### Scenario: A corrupted download

- **WHEN** the downloaded artifact does not match its `.sha256`
- **THEN** the installer stops, saying so, and nothing is installed or removed

#### Scenario: Upgrading

- **WHEN** a researcher runs the command of a newer release than the one installed, with VISTA
  closed
- **THEN** the newer package replaces the older one in the same place, their chats and settings
  are kept, and the app-menu entries still open VISTA

#### Scenario: Upgrading while VISTA is running

- **WHEN** a researcher runs the install command while VISTA is open
- **THEN** the installer says to close VISTA and run the command again, and changes nothing

#### Scenario: An install from an earlier layout

- **WHEN** the installer runs on a machine holding a package installed under an earlier layout,
  such as a version-named folder under `~/.local/share/vista`, or `/Applications/VISTA/` on
  macOS
- **THEN** after the new package is installed, that folder is removed, and VISTA's state directory
  is untouched

#### Scenario: An unsupported machine

- **WHEN** the installer runs on a platform with no package
- **THEN** it stops before downloading, naming the platforms that have one
