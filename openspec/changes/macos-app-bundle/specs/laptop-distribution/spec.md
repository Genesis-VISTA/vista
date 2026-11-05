## MODIFIED Requirements

### Requirement: Mutable state lives outside the artifact

All state the running application creates or modifies SHALL live outside the unpacked
artifact. The artifact's own contents SHALL be treated as read-only at run time: no file in it
SHALL be created, changed or removed by starting, using or stopping VISTA, including compiled
bytecode and caches.

#### Scenario: Copying carries no personal data

- **WHEN** an artifact that has been run is copied to another machine
- **THEN** the recipient's instance contains none of the sender's conversations, uploaded
  files, or configured credentials

#### Scenario: Replacing the artifact preserves state

- **WHEN** the unpacked artifact is replaced with a newer version whose database is compatible
  with the existing one, and started
- **THEN** existing conversations, uploaded files, and configured credentials remain
  available

#### Scenario: Replacing the artifact with an incompatible version

- **WHEN** the unpacked artifact is replaced with a newer version that cannot use the existing
  database, and started
- **THEN** startup stops before changing the database, and the startup window offers to move
  the local data into a backup folder and start fresh, without the researcher moving or
  deleting files by hand

#### Scenario: A session leaves the artifact unchanged

- **WHEN** an artifact is started, used for a chat that runs code in the sandbox and searches
  a knowledge base, and stopped
- **THEN** every file in the artifact is as it was before, and on macOS `VISTA.app` still
  passes strict signature verification

### Requirement: Each package has an application entry point

Every artifact SHALL contain an application entry point and a diagnostic command-line launcher,
and its manifest SHALL name both. The macOS artifact SHALL be a single self-contained
`VISTA.app` that holds the whole package, runtime and payload included, so it can be moved or
copied as one item; its diagnostic launcher SHALL be inside it. On Linux and Windows the
application SHALL find the rest of the package relative to itself and SHALL explain when it has
been separated from it. On every platform the application, like the runtime, SHALL treat the
package as read-only.

#### Scenario: Opening the application

- **WHEN** the researcher opens the application entry point of an installed or unpacked package
- **THEN** VISTA starts through its startup window, without a terminal or console

#### Scenario: Moving the macOS application

- **WHEN** a researcher moves or copies `VISTA.app` to another folder and opens it there
- **THEN** VISTA starts normally, with nothing else needing to move with it

#### Scenario: Application separated from its package

- **WHEN** the Linux or Windows application is launched after being moved away from the rest
  of its package
- **THEN** it explains that the whole VISTA folder must stay together, and starts no service

#### Scenario: Diagnostic command-line launch

- **WHEN** support or package automation runs the diagnostic launcher
- **THEN** it performs the textual startup flow in its terminal, without changing the
  application's normal entry point
