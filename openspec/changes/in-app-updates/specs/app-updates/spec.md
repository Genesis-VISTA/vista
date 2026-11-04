## Purpose

Lets a researcher see which VISTA they are running, learn when a newer release exists, and
move to it from inside VISTA without losing their state or being left without a working
install.

## ADDED Requirements

### Requirement: The running version is reported

The backend SHALL report the installed version and the commit it was built from, the same
version string the package's manifest and launcher carry. A development checkout SHALL report
its version as `dev` together with its commit when one is known.

#### Scenario: A released package

- **WHEN** a researcher runs the 0.2.5 release package and asks the backend for its version
- **THEN** it reports `0.2.5` and the release's commit

#### Scenario: A development checkout

- **WHEN** VISTA runs from a checkout with `./launch.sh`
- **THEN** the backend reports `dev` and the checkout's commit

### Requirement: Updates are checked against published GitHub releases

When checking is enabled, the backend SHALL look up the newest published release of
`Genesis-VISTA/vista` on GitHub when it starts, at least 24 hours after its last successful
check while it runs, and whenever the researcher asks it to check now. Drafts SHALL never be
considered. Prereleases SHALL be considered only when the researcher has opted into them. A
release SHALL be offered only when its version is newer than the installed one by semantic
versioning precedence, ignoring the installed version's build metadata. The base URL of the
GitHub API SHALL be configurable so tests can serve a fake release.

#### Scenario: A newer stable release

- **WHEN** 0.2.3 is installed and 0.2.5 is the newest published release
- **THEN** the backend reports 0.2.5 as available, with its release notes link

#### Scenario: Already current

- **WHEN** 0.2.5 is installed and 0.2.5 is the newest published release
- **THEN** the backend reports no update

#### Scenario: A prerelease without opting in

- **WHEN** 0.2.5 is installed, 0.2.6-rc1 is published as a prerelease, and the researcher
  has not opted into prereleases
- **THEN** the backend reports no update

#### Scenario: A prerelease after opting in

- **WHEN** the same researcher opts into prereleases and checks now
- **THEN** the backend reports 0.2.6-rc1 as available

#### Scenario: A release candidate is older than its release

- **WHEN** 0.2.2-rc1 is installed and 0.2.2 is published
- **THEN** the backend reports 0.2.2 as available

### Requirement: Checking can be turned off and never fails loudly

Checking SHALL be on for a new install and SHALL be turned off by a per-install setting that
persists across restarts. While it is off, VISTA SHALL make no request to GitHub, including on
startup. A check that cannot reach GitHub, is rate-limited, or gets a response it cannot read
SHALL leave VISTA working, SHALL report no update rather than an error to the page, and SHALL
record why in the backend log.

#### Scenario: Turned off

- **WHEN** a researcher turns update checks off and restarts VISTA
- **THEN** no request reaches the GitHub API, and About says checks are off

#### Scenario: Offline

- **WHEN** the machine has no network and VISTA starts
- **THEN** VISTA starts as usual, About shows the last successful check time or none, and no
  error is shown

### Requirement: Development and local builds do not check

A build whose version is `dev`, carries build metadata (such as `0.2.4+3.g1a2b3c4`), or is
marked `-dirty` SHALL NOT check for updates or offer to update, whatever the setting.

#### Scenario: A local package build

- **WHEN** a researcher runs a package built locally as `0.2.4+3.g1a2b3c4`
- **THEN** no request reaches the GitHub API and About says update checks are off for
  development builds

### Requirement: An update is downloaded and verified before VISTA stops

Asking to update SHALL download the release's archive for this platform, its `.sha256` file
and the release's installer into a cache under VISTA's state directory while VISTA keeps
running, reporting progress. Each file SHALL be checked against the sha256 digest the GitHub
API reports for that asset, and the archive also against its `.sha256`. A file that fails
either check SHALL be deleted and the update SHALL stop with the reason, with VISTA still
running. Nothing SHALL be downloaded until the researcher asks. The cache SHALL be removed
after a successful update.

#### Scenario: A clean download

- **WHEN** a researcher clicks Update to 0.2.5
- **THEN** progress is shown while VISTA stays usable, and when all three files match their
  digests the researcher is asked to restart

#### Scenario: A tampered asset

- **WHEN** a downloaded file's sha256 differs from the digest GitHub reports
- **THEN** the file is deleted, VISTA says the download failed verification, and nothing is
  installed

#### Scenario: Not enough disk

- **WHEN** the cache cannot hold the archive
- **THEN** the download stops with a message naming the free space needed, and VISTA keeps
  running

### Requirement: Restarting to update says what it will interrupt

Before VISTA stops to install, the researcher SHALL be asked to confirm. The confirmation
SHALL list work in progress that restarting interrupts or pauses: running agent responses,
running debates, and HPC jobs VISTA is watching (which keep running on the cluster). When the
release's recorded data compatibility shows the installed version's database will not be
accepted, the confirmation SHALL say the update will ask to back up and reset local data.

#### Scenario: Work in progress

- **WHEN** an agent response is streaming and one Frontier job is being watched, and the
  researcher clicks Restart to update
- **THEN** the confirmation names the running conversation and the watched job, and nothing
  stops until they confirm

#### Scenario: A data reset ahead

- **WHEN** 0.2.1 is installed and 0.2.5 records it accepts databases from 0.2.3 onward
- **THEN** the confirmation says the update will ask to back up and reset local data

#### Scenario: Cancelled

- **WHEN** the researcher cancels the confirmation
- **THEN** VISTA keeps running and the downloaded update stays ready

### Requirement: The update replaces the install and reopens VISTA

After confirmation, VISTA SHALL stop every process it started, exactly as when the window is
closed, and then install the cached release with that release's own installer into the same
install folder, leaving VISTA's state directory untouched. On success the new version SHALL
open without further action. The hand-off SHALL work on every supported platform.

#### Scenario: A successful update

- **WHEN** a researcher on 0.2.3 confirms Restart to update to 0.2.5
- **THEN** VISTA closes, 0.2.5 opens from the same app-menu entry, About reports 0.2.5, and
  their chats and settings are kept

### Requirement: A failed update leaves the old version running

If the install fails after VISTA has stopped, the previously installed version SHALL still be
in place and SHALL be reopened. Its startup window SHALL say the update to the named version
failed and why, and SHALL offer to open the update log and copy diagnostics, as it does for a
failed startup.

#### Scenario: The installer fails

- **WHEN** the installer exits with an error during the update to 0.2.5
- **THEN** 0.2.3 reopens, its startup window says the update to 0.2.5 failed with the reason,
  and Open logs shows the update log
