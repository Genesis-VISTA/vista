## MODIFIED Requirements

### Requirement: The window is VISTA's lifetime

On macOS, a startup window SHALL open immediately when the application is launched and the main
VISTA interface SHALL open only after every service answers. On other platforms, the existing
window readiness behaviour SHALL remain unchanged. Closing the startup window or main window,
including through the application's Quit command, SHALL stop every process VISTA started,
including code-execution sandboxes started on behalf of agent sessions. Interrupting a diagnostic
or non-macOS terminal launcher SHALL close its window and stop the processes it started.

#### Scenario: macOS startup window opens before services

- **WHEN** the researcher launches `VISTA.app` on macOS
- **THEN** a VISTA startup window appears without waiting for service readiness and no Terminal
  window opens

#### Scenario: Window opens when services are ready

- **WHEN** every service answers during macOS graphical startup
- **THEN** the startup window is replaced by one main window showing the VISTA interface at its
  established 1280 × 860 default size rather than the compact startup window's size

#### Scenario: Closing during startup stops VISTA

- **WHEN** the researcher closes the startup window or chooses Quit before startup completes
- **THEN** no process VISTA started remains running or holds any of VISTA's ports

#### Scenario: Closing the window stops VISTA

- **WHEN** the researcher closes the main VISTA window or chooses Quit
- **THEN** no process VISTA started remains running or holds any of VISTA's ports

#### Scenario: Immediate restart

- **WHEN** VISTA is started again immediately after either kind of stop
- **THEN** the port preflight reports no conflict

#### Scenario: Interrupting the terminal closes the window

- **WHEN** the researcher presses Ctrl-C in a diagnostic or non-macOS launcher terminal, or closes
  that terminal
- **THEN** the window closes and no process that launcher started remains running

#### Scenario: Non-macOS readiness behaviour

- **WHEN** the launcher is run on Linux or Windows and all services become healthy
- **THEN** its window opens according to the existing platform flow, with no macOS startup
  renderer or protocol required

### Requirement: One window per installation

Only one VISTA application instance SHALL start services at a time for a given user. A second
launch SHALL focus whichever window the existing instance is currently showing.

#### Scenario: Second launch during initialization

- **WHEN** `VISTA.app` is launched again while the first instance shows startup activity
- **THEN** the existing startup window is brought to the front and no second launcher or service
  process is started

#### Scenario: Second launch after startup

- **WHEN** `VISTA.app` is launched again while the main VISTA window is open
- **THEN** the main window is brought to the front and no second launcher or service process is
  started

#### Scenario: Second launch while running

- **WHEN** a second VISTA window is started while one is already open on any supported platform
- **THEN** the existing window is brought to the front and the second exits without starting a
  second service stack

## ADDED Requirements

### Requirement: macOS startup reports real activity

The macOS startup window SHALL report the actual initialization phase received from a versioned,
machine-readable launcher protocol. It SHALL NOT derive state by parsing human log messages and
SHALL NOT show a percentage or remaining-time estimate when the launcher cannot measure one.

#### Scenario: First-run activity

- **WHEN** bundled resources or the sandbox image need first-run preparation
- **THEN** the startup window identifies the active preparation step and marks it complete only
  after the launcher reports completion

#### Scenario: Subsequent startup skips completed work

- **WHEN** a resource is already installed or an image is already imported
- **THEN** the activity is reported as completed or skipped without repeating the work

#### Scenario: Unsupported protocol

- **WHEN** the application receives a startup protocol version it does not support
- **THEN** startup stops with an application-version error instead of guessing how to interpret
  the events

### Requirement: macOS startup failures are actionable in the application

An expected startup failure SHALL remain in the startup window and identify the failed activity,
a concise cause and available remedy. The researcher SHALL be able to open the log location, retry
after cleanup, or quit. Diagnostics offered for copying SHALL exclude credentials and environment
values.

#### Scenario: A service does not become healthy

- **WHEN** a VISTA service fails or exceeds its startup health timeout
- **THEN** the startup window names that service, offers its log location, and offers Retry and
  Quit without opening Terminal

#### Scenario: Retry after remedy

- **WHEN** the researcher remedies a reported condition and chooses Retry
- **THEN** a fresh startup begins only after the failed launcher's cleanup has completed

#### Scenario: Copying diagnostics

- **WHEN** the researcher chooses Copy Diagnostics for a startup failure
- **THEN** the copied summary includes versions, phase, error code and log paths but no credential,
  secret or environment value

### Requirement: Startup privileges do not reach the VISTA page

The local startup renderer SHALL receive only the capabilities needed to observe startup and invoke
its fixed controls. The main VISTA page SHALL continue to have no preload bridge, local filesystem,
process or application-internal access.

#### Scenario: Transition to the main interface

- **WHEN** the startup renderer is replaced by the main VISTA interface
- **THEN** none of the startup renderer's launcher, log, clipboard or retry capabilities are
  exposed to the main VISTA page
