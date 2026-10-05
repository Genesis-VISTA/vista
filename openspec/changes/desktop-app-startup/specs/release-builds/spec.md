## MODIFIED Requirements

### Requirement: Release notes describe what was built

Every draft release SHALL carry generated notes that start with the one-line install command for
each platform. They SHALL then state each platform's archive with its sha256, the build-inputs
commit, and how to download, verify, install and open each platform's package by hand. On macOS
they SHALL say to download with `curl` rather than a browser, end with opening `VISTA.app`, and
say how to open a package that Gatekeeper blocks. A maintainer adds the description of what
changed.

#### Scenario: Reading a draft's notes

- **WHEN** a maintainer opens a draft release
- **THEN** its notes start with the install commands for macOS and Linux and for Windows, pinned
  to that release's tag, and list the three archives with their sha256 values, the build-inputs
  commit, the manual install steps per platform and the macOS quarantine workaround, with a
  placeholder for the changes

#### Scenario: Installing by hand on macOS

- **WHEN** a researcher follows the notes' manual macOS steps
- **THEN** they download with `curl`, check the sha256, unpack the package into
  `~/Applications/VISTA`, and start VISTA by opening `VISTA.app`

### Requirement: A release installs with one command

Every release SHALL carry an installer for macOS and Linux and one for Windows, each with that
release's version written in, so one command downloads, verifies, installs and starts that
release's package for the machine it runs on. An installer SHALL install into one fixed folder per
platform, whatever the version: `~/Applications/VISTA` on macOS, `~/.local/share/vista/app` on
Linux, and `%LOCALAPPDATA%\VISTA\app` on Windows. It SHALL make VISTA launchable from that
platform's usual place, with VISTA's icon.

An installer SHALL NOT unpack an archive that does not match its `.sha256`. It SHALL leave a
previously installed version in place until the new one is, SHALL refuse to replace a version
that is running, and SHALL NOT touch VISTA's state directory. After a successful install it SHALL
remove package folders left by earlier install layouts. Run again for a version already installed,
it SHALL start that copy without downloading. When it starts VISTA, it SHALL start the application
entry point rather than the diagnostic launcher.

#### Scenario: Installing the newest release

- **WHEN** a researcher on a supported machine runs the install command from the README
- **THEN** the newest published release's package for that machine is downloaded, checked,
  installed into its platform's fixed folder, and VISTA's application opens

#### Scenario: Launchable after installing

- **WHEN** an install completes
- **THEN** VISTA is in Spotlight and Launchpad on macOS, in the app menu on Linux, and in the Start
  menu on Windows, each with VISTA's icon

#### Scenario: A corrupted download

- **WHEN** the downloaded archive does not match its `.sha256`
- **THEN** the installer stops, saying so, and nothing is installed or removed

#### Scenario: Upgrading

- **WHEN** a researcher runs the command of a newer release than the one installed, with VISTA
  closed
- **THEN** the newer package replaces the older one in the same folder, their chats and settings
  are kept, and the app-menu entries still open VISTA

#### Scenario: Upgrading while VISTA is running

- **WHEN** a researcher runs the install command while VISTA is open
- **THEN** the installer says to close VISTA and run the command again, and changes nothing

#### Scenario: An install from an earlier layout

- **WHEN** the installer runs on a machine holding a package installed under an earlier layout,
  such as a version-named folder under `~/.local/share/vista`
- **THEN** after the new package is installed, that folder is removed, and VISTA's state directory
  is untouched

#### Scenario: An unsupported machine

- **WHEN** the installer runs on a platform with no package
- **THEN** it stops before downloading, naming the platforms that have one
