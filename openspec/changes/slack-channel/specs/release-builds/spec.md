## MODIFIED Requirements

### Requirement: A release installs with one command

Every release SHALL carry an installer for macOS and Linux and one for Windows, each with that
release's version written in, so one command downloads, verifies, installs and starts that
release's package for the machine it runs on. An installer SHALL install into one fixed folder per
platform, whatever the version: on macOS `/Applications/VISTA`, or `~/Applications/VISTA` when the
installer cannot write there; `~/.local/share/vista/app` on Linux; and `%LOCALAPPDATA%\VISTA\app`
on Windows. It SHALL make VISTA launchable from that platform's usual place, with VISTA's icon.

An installer SHALL NOT unpack an archive that does not match its `.sha256`. It SHALL leave a
previously installed version in place until the new one is, SHALL refuse to replace a version
that is running other than as VISTA's background service, SHALL stop that service before
replacing the package and start it again afterwards, and SHALL NOT touch VISTA's state directory. After a successful install it SHALL
remove package folders left by earlier install layouts, including on macOS the researcher's own
`~/Applications/VISTA` once VISTA is installed in `/Applications/VISTA`. It SHALL NOT remove a
`/Applications/VISTA` it did not install into. Run again for a version already installed,
it SHALL start that copy without downloading. When it starts VISTA, it SHALL start the application
entry point rather than the diagnostic launcher.

#### Scenario: Installing the newest release

- **WHEN** a researcher on a supported machine runs the install command from the README
- **THEN** the newest published release's package for that machine is downloaded, checked,
  installed into its platform's fixed folder, and VISTA's application opens

#### Scenario: Launchable after installing

- **WHEN** an install completes
- **THEN** VISTA is in Spotlight and the Apps view (Launchpad) on macOS, in the app menu on Linux,
  and in the Start menu on Windows, each with VISTA's icon

#### Scenario: Visible in Finder's Applications on macOS

- **WHEN** an administrator installs VISTA on a Mac
- **THEN** it is installed in `/Applications/VISTA`, so Finder's Applications shows it in a `VISTA`
  folder, without `sudo`

#### Scenario: Installing on a Mac without administrator rights

- **WHEN** the installer cannot write to `/Applications`, or to an existing `/Applications/VISTA`
  that another account installed
- **THEN** it installs into `~/Applications/VISTA`, says why, and leaves `/Applications/VISTA`
  untouched; VISTA is still in Spotlight and the Apps view

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


#### Scenario: Upgrading with background mode on

- **WHEN** a researcher runs the install command while VISTA's background service runs and no
  window is open
- **THEN** the installer stops the service, installs the new package, and starts the service
  again from it

#### Scenario: An install from an earlier layout

- **WHEN** the installer runs on a machine holding a package installed under an earlier layout,
  such as a version-named folder under `~/.local/share/vista`
- **THEN** after the new package is installed, that folder is removed, and VISTA's state directory
  is untouched

#### Scenario: An unsupported machine

- **WHEN** the installer runs on a platform with no package
- **THEN** it stops before downloading, naming the platforms that have one
