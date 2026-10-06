## MODIFIED Requirements

### Requirement: Single command to install and run

Outstanding first-run setup and VISTA startup SHALL take one action: launching the application
from the platform's usual place, which needs no terminal or console. That action SHALL be safe to
repeat, and on a subsequent run SHALL skip work already done. It SHALL end by opening VISTA in its
application window; VISTA has no browser mode. The package's diagnostic launcher SHALL offer the
same setup and startup from a terminal, and SHALL refuse a session that cannot show the window
before any service starts.

#### Scenario: First run

- **WHEN** the researcher launches VISTA for the first time after installing it
- **THEN** the application prepares the sandbox image, creates the state directory, installs the
  bundled corpus, seeds the database, starts the services, and opens the main VISTA interface,
  reporting progress in its startup window

#### Scenario: Subsequent run

- **WHEN** the researcher launches VISTA against an already initialised state directory
- **THEN** it starts the services without repeating setup and without re-importing the sandbox
  image

#### Scenario: No window available

- **WHEN** the diagnostic launcher is run in a session that cannot show the window, such as one
  with no display or a remote shell session
- **THEN** it says why the window cannot open and exits without starting any service

### Requirement: Build-time verification

The build SHALL verify its own prerequisites before producing an artifact, and SHALL verify the
finished artifact before it is considered complete. Verification SHALL include starting the
artifact from a location other than the one it was built in, and SHALL include the supervised
startup protocol the application depends on. Verification SHALL NOT require code signing.

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
  unless it was built with the science projects enabled, and shut down before the build reports
  success

#### Scenario: The supervised protocol is exercised

- **WHEN** a build completes, on any platform
- **THEN** the relocated artifact's launcher is run in supervised mode without a window, its events
  are checked to arrive in order through to UI readiness with the interface's address, a deliberate
  port conflict is checked to be reported as a preflight failure with its code, and a stop request
  is checked to leave no process running and no port held

#### Scenario: Contents are recorded

- **WHEN** a build completes
- **THEN** it records the components included and their sizes, and each platform's application
  entry point and diagnostic launcher, so an incomplete artifact can be identified without
  unpacking it

### Requirement: Windows x64 is a supported platform

VISTA SHALL be distributable as an artifact for Windows 10 and 11 on x64. The artifact SHALL meet
every other requirement of this capability. Its application entry point SHALL be `VISTA.exe`,
started from the Start menu, and SHALL open no console window. Its diagnostic launcher SHALL be
`vista.cmd`, which needs no shell other than cmd or PowerShell.

#### Scenario: First run on Windows

- **WHEN** a Windows x64 artifact has been installed and VISTA is chosen from the Start menu, on a
  host that meets its preconditions
- **THEN** it performs first-run setup, starts every service including the code-execution sandbox,
  and opens the VISTA window, with no console window at any point

#### Scenario: Diagnostic launch on Windows

- **WHEN** `vista.cmd` is run from cmd, PowerShell or Explorer
- **THEN** it performs the same setup and startup from its console and opens the VISTA window, as
  before

#### Scenario: Agent round trip on Windows

- **WHEN** a researcher on a Windows host sends an agent a message that runs code, writes a plot
  and displays it
- **THEN** the plot is displayed in the interface

## ADDED Requirements

### Requirement: Each package has an application entry point

Every artifact SHALL contain an application entry point and a diagnostic command-line launcher,
and its manifest SHALL name both. The macOS artifact SHALL place `VISTA.app` at its top level. The
application SHALL find the rest of the package relative to itself, SHALL explain when it has been
separated from it, and, like the runtime, SHALL treat the package as read-only.

#### Scenario: Opening the application

- **WHEN** the researcher opens the application entry point of an installed or unpacked package
- **THEN** VISTA starts through its startup window, without a terminal or console

#### Scenario: Application separated from its package

- **WHEN** the application is launched after being moved away from the rest of its package
- **THEN** it explains that the whole VISTA folder must stay together, and starts no service

#### Scenario: Diagnostic command-line launch

- **WHEN** support or package automation runs the diagnostic launcher
- **THEN** it performs the textual startup flow in its terminal, without changing the
  application's normal entry point
