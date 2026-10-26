## MODIFIED Requirements

### Requirement: Startup failures are actionable in the application

An expected startup failure SHALL remain in the startup window and identify the failed activity,
a concise cause and the available remedy. The researcher SHALL be able to open the log location,
retry after cleanup, or quit. Diagnostics offered for copying SHALL exclude credentials and
environment values.

#### Scenario: A service does not become healthy

- **WHEN** a VISTA service fails or exceeds its startup health timeout
- **THEN** the startup window names that service, offers its log location, and offers Retry and
  Quit without opening a terminal

#### Scenario: A service exits during startup

- **WHEN** a VISTA service exits before it becomes healthy
- **THEN** the startup window reports the failure once the service has exited, without
  waiting for that service's health timeout

#### Scenario: Incompatible local data

- **WHEN** the backend refuses to start because the local data is incompatible with this version
- **THEN** the startup window says "Your local data is incompatible with this version of VISTA,
  please reset", and offers Back up and reset, Open Logs and Quit

#### Scenario: Retry after remedy

- **WHEN** the researcher remedies a reported condition and chooses Retry
- **THEN** a fresh startup begins only after the failed launcher's cleanup has completed

#### Scenario: Copying diagnostics

- **WHEN** the researcher chooses Copy Diagnostics for a startup failure
- **THEN** the copied summary includes versions, phase, error code and log paths but no
  credential, secret or environment value

#### Scenario: Windows allows only signed scripts

- **WHEN** VISTA is launched on a Windows host whose execution policy is `AllSigned`
- **THEN** the startup window says that Group Policy lets PowerShell run only signed scripts and
  that an administrator must allow VISTA's launcher, and no service is started

## ADDED Requirements

### Requirement: Incompatible local data can be reset from the startup window

When startup fails because the local data is incompatible, the startup window SHALL offer Back up
and reset, and SHALL offer it for no other failure. Before acting, it SHALL ask for confirmation,
saying that conversations, projects and settings won't carry over and that everything will be
kept in a backup folder. A reset SHALL act only once the failed launcher has finished stopping.
It SHALL move the researcher's local data out of the state directory into a new backup folder
whose name identifies when it was made. That covers the database, knowledge bases, skills,
project inputs and outputs, and everything else that is not a download cache or a log. It SHALL
then begin a fresh startup. Nothing SHALL be deleted. Once the data is moved, the window SHALL
show the backup folder's location, say that the researcher's skills, knowledge bases and project
files are there, and offer to open it. The backup folder SHALL contain a README explaining where
each of those is inside it. The README SHALL list the researcher's own skills, knowledge bases and
projects first, separately from the ones VISTA ships and recreates on a fresh start, and SHALL
list only folders the backup holds. It SHALL open with a prompt the researcher can give a coding
agent (such as Claude Code or Codex) to restore their work into the running VISTA. The prompt
SHALL have the agent leave the backup unchanged, write only through the running VISTA's own
interface, leave settings and credentials to the researcher, and confirm its plan with the
researcher before writing anything.

#### Scenario: Back up and reset

- **WHEN** the researcher chooses Back up and reset and confirms
- **THEN** the local data is moved into a dated backup folder, the window names that folder and
  offers to open it, a fresh startup begins, and VISTA opens as on a first run

#### Scenario: Reset is cancelled

- **WHEN** the researcher chooses Back up and reset and then cancels
- **THEN** nothing is moved and the startup window still shows the failure

#### Scenario: The backup holds the researcher's work

- **WHEN** the researcher opens the backup folder after a reset
- **THEN** it holds the old database, every knowledge base, every skill and every project's
  inputs and outputs, and a README saying where each is

#### Scenario: The README puts the researcher's work first

- **WHEN** the backup holds skills, knowledge bases and projects both of the researcher's own
  and of VISTA's bundled or seeded ones
- **THEN** the README lists the researcher's own first with their folders, names VISTA's own
  separately as coming back with VISTA, and lists no folder the backup does not hold

#### Scenario: A restore prompt for a coding agent

- **WHEN** the researcher opens the README
- **THEN** it begins with a prompt to paste into a coding agent, which tells the agent to
  restore the researcher's own work into the running VISTA through its API, to leave the backup
  folder unchanged, to skip settings and credentials, and to confirm its plan with the
  researcher before writing anything

#### Scenario: Caches are kept

- **WHEN** a reset completes
- **THEN** downloaded models, the sandbox runtime, the bundled data payload and the logs are
  still in the state directory, and the fresh startup does not download or rebuild them

#### Scenario: Earlier backups are kept

- **WHEN** a reset runs on an installation that already holds a backup folder from an earlier
  reset
- **THEN** the earlier backup is left where it is and the new one is made beside it

#### Scenario: Reset is not offered for other failures

- **WHEN** startup fails for any other reason
- **THEN** the startup window does not offer Back up and reset
