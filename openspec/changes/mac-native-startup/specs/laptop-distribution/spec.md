## MODIFIED Requirements

### Requirement: Single command to install and run

Outstanding first-run setup and VISTA startup SHALL require one primary user action. On macOS that
action SHALL be launching the top-level `VISTA.app` from Finder, Spotlight or the Dock; it SHALL
not require Terminal or a command. On Linux and Windows the existing platform entrypoints SHALL
remain unchanged. Repeated startup SHALL skip work already completed.

#### Scenario: First run

- **WHEN** the researcher launches `VISTA.app` for the first time after unpacking
- **THEN** the application prepares the sandbox image, creates the state directory, installs the
  bundled corpus, seeds the database, starts the services and opens the main VISTA interface while
  reporting progress graphically

#### Scenario: Subsequent run

- **WHEN** the researcher launches `VISTA.app` against an already initialized state directory
- **THEN** the application starts the services without repeating setup or re-importing the sandbox
  image

#### Scenario: Existing Linux entrypoint

- **WHEN** the Linux artifact is started through its documented launcher
- **THEN** its startup, window sandbox and failure behaviour are unchanged by the macOS graphical
  entrypoint

#### Scenario: Existing Windows entrypoint

- **WHEN** the Windows artifact is started through `vista.cmd`
- **THEN** its startup, console and failure behaviour are unchanged by the macOS graphical
  entrypoint

#### Scenario: No window available

- **WHEN** a platform launcher is started in a session that cannot show its window, such as one
  with no display or a remote shell session
- **THEN** it says why the window cannot open and exits without starting any service

### Requirement: Build-time verification

The build SHALL verify its own prerequisites before producing an artifact and SHALL verify the
finished artifact before it is considered complete. Verification SHALL include starting the
artifact from a location other than the one it was built in. A macOS release artifact SHALL also
be Developer ID signed, notarized, ticket-stapled and accepted by Gatekeeper, and SHALL retain a
working code-execution sandbox with its required entitlement.

#### Scenario: A build prerequisite is missing

- **WHEN** a build is started without one of its required credentials or tools, or without the
  AI-safety corpus in its source data
- **THEN** it fails immediately, naming what is missing, rather than producing an incomplete
  artifact

#### Scenario: Science data required only when enabled

- **WHEN** a build is started without the science projects enabled and its source data lacks the
  molten-salt corpus or MSTDB
- **THEN** the build proceeds

#### Scenario: The finished artifact is exercised

- **WHEN** a build completes
- **THEN** the artifact is unpacked to a different location, started, checked for service health,
  checked with one retrieval query against the AI-safety corpus, checked to contain no science data
  unless built with the science projects enabled, and shut down before the build reports success

#### Scenario: macOS graphical artifact is verified

- **WHEN** a macOS release artifact completes
- **THEN** its stapled notarization ticket and Gatekeeper assessment pass, launching `VISTA.app`
  opens no Terminal window, and the application reaches its main interface

#### Scenario: Sandbox entitlement survives release signing

- **WHEN** the macOS release signing and notarization steps complete
- **THEN** the bundled sandbox runtime retains the complete entitlement set required by its build
  and successfully starts a real sandbox during package validation

#### Scenario: Signing credentials are absent

- **WHEN** a release macOS build is requested without its signing or notarization credentials
- **THEN** the build fails before producing a release artifact rather than silently emitting an
  ad-hoc-signed package

#### Scenario: Contents are recorded

- **WHEN** a build completes
- **THEN** it records the components included and their sizes, so an incomplete artifact can be
  identified without unpacking it

## ADDED Requirements

### Requirement: macOS package has a native application entrypoint

The macOS artifact SHALL place `VISTA.app` at the top level as its documented primary entrypoint.
It SHALL retain a diagnostic command-line launcher, and the application SHALL detect and explain
when it has been separated from the runtime it supervises. The application and runtime SHALL treat
the unpacked package as read-only.

#### Scenario: Launch from Finder

- **WHEN** the researcher double-clicks the top-level `VISTA.app`
- **THEN** VISTA starts through its graphical initialization experience without Terminal

#### Scenario: App separated from runtime

- **WHEN** the researcher launches `VISTA.app` after moving it away from its packaged runtime
- **THEN** the application explains that the whole unpacked VISTA folder must stay together and
  starts no service

#### Scenario: Diagnostic command-line launch

- **WHEN** support or package automation invokes the retained `vista` launcher
- **THEN** it can perform the existing textual startup flow without changing the graphical
  application's normal entrypoint
