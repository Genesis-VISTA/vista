# release-builds Specification

## Purpose
Turns a version tag into a published set of VISTA packages, one per supported platform, built
and verified the same way every time on GitHub-hosted runners rather than by hand.

## Requirements

### Requirement: A version tag builds every release platform

Pushing a tag of the form `v<semver>` SHALL build the linux-x86_64, mac-arm64 and
windows-x86_64 packages and SHALL create one draft GitHub release for that tag. The draft
SHALL carry each platform's archive and its `.sha256` file. No release SHALL be created
unless all three platforms built and passed their verification. A private rehearsal
repository MAY narrow the set of platforms it builds, but a public repository SHALL NOT
create a release from fewer than all three.

#### Scenario: A release tag is pushed

- **WHEN** the tag `v0.2.0` reaches the GitHub repository
- **THEN** a draft release named for `v0.2.0` is created with `vista-0.2.0-linux-x86`,
  `vista-0.2.0-mac-arm64` and `vista-0.2.0-win-x86` archives, each with a matching
  `.sha256` file

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

### Requirement: Manual builds create no release

A build started by hand SHALL be able to target any branch, SHALL run the same build and
verification as a tagged build, and SHALL make its archives available only as short-lived
run artifacts, never as a release.

#### Scenario: Building a branch by hand

- **WHEN** a maintainer starts a build by hand on a branch
- **THEN** the archives for every release platform are available from that run, and no
  release or tag is created

### Requirement: The tag is the release version

A tagged build SHALL take its version from the tag, without the leading `v`. That version
SHALL appear in the archive names, in each package's recorded contents and in what the
running package reports. No file in the repository SHALL need editing to make a release.

#### Scenario: Version carried from the tag

- **WHEN** the package built from tag `v0.2.0` is unpacked and started
- **THEN** its manifest, its launcher output and its running services all report `0.2.0`

### Requirement: Releases are published only by a maintainer

A release SHALL stay a draft until a maintainer publishes it. Before a release is published,
the full verification, including the checks that go through the code-execution sandbox,
SHALL have passed for every platform. Where a build host could not run the sandbox, that
verification SHALL be done by a maintainer on a machine of that platform, from the archive
attached to the draft.

#### Scenario: A platform verified without the sandbox

- **WHEN** a draft's macOS archive was verified on a host that could not run the sandbox
- **THEN** the release notes say so, and the release is published only after that archive
  passes the full smoke test on a real machine of that platform

#### Scenario: Nothing is published automatically

- **WHEN** every platform of a tagged build succeeds
- **THEN** the release remains a draft, visible only to people with write access to the
  repository, until a maintainer publishes it

### Requirement: Release inputs are pinned

Everything a release build takes from outside the repository SHALL be fixed by an
identifier recorded in the repository: the AI-safety corpus with its prebuilt vector store,
by a commit of the private build-inputs repository, and the embedding weights, by revision. A
build SHALL fail rather than build with an input that does not match its pin. The build
inputs SHALL NOT be published or offered for download on their own. They reach researchers
only inside the packages.

#### Scenario: The pinned inputs are unavailable

- **WHEN** the pinned build-inputs commit cannot be fetched, because it is missing or access
  is refused
- **THEN** the build fails before building any package, naming the commit it could not fetch

#### Scenario: Looking for the corpus outside a package

- **WHEN** someone without access to the build-inputs repository looks for the corpus or the
  vector store on GitHub
- **THEN** they find neither, except inside a published package

#### Scenario: Two builds of the same tag

- **WHEN** the same tag is built twice
- **THEN** both builds ship the same corpus, vector store and embedding weights

### Requirement: Release builds hold no institutional credentials

A release build SHALL need no ORNL credential and no LLM inference credential. The only
secrets it SHALL use are read-only credentials, each scoped to one repository: the
repositories its bundled dependencies come from, and the build-inputs repository.

#### Scenario: Inspecting what a release build needs

- **WHEN** the secrets available to the release workflow are listed
- **THEN** the list contains only read-only repository access, with no code.ornl.gov token
  and no inference key

### Requirement: Release builds run on fixed host versions

Each platform SHALL be built on a named, fixed host OS version, so the compatibility floor a
package records changes only when that version is changed on purpose.

#### Scenario: The hosted default moves

- **WHEN** the CI provider changes which OS version its default runner label points to
- **THEN** release builds still run on the versions they name, and the recorded floor of the
  next release is unchanged

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
  `/Applications/VISTA` (or `~/Applications/VISTA` without administrator rights), and start
  VISTA by opening `VISTA.app`

### Requirement: A release installs with one command

Every release SHALL carry an installer for macOS and Linux and one for Windows, each with that
release's version written in, so one command downloads, verifies, installs and starts that
release's package for the machine it runs on. An installer SHALL install into one fixed folder per
platform, whatever the version: on macOS `/Applications/VISTA`, or `~/Applications/VISTA` when the
installer cannot write there; `~/.local/share/vista/app` on Linux; and `%LOCALAPPDATA%\VISTA\app`
on Windows. It SHALL make VISTA launchable from that platform's usual place, with VISTA's icon.

An installer SHALL NOT unpack an archive that does not match its `.sha256`. It SHALL leave a
previously installed version in place until the new one is, SHALL refuse to replace a version
that is running, and SHALL NOT touch VISTA's state directory. After a successful install it SHALL
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

#### Scenario: An install from an earlier layout

- **WHEN** the installer runs on a machine holding a package installed under an earlier layout,
  such as a version-named folder under `~/.local/share/vista`
- **THEN** after the new package is installed, that folder is removed, and VISTA's state directory
  is untouched

#### Scenario: An unsupported machine

- **WHEN** the installer runs on a platform with no package
- **THEN** it stops before downloading, naming the platforms that have one
