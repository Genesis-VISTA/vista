## MODIFIED Requirements

### Requirement: The window is VISTA's lifetime

When the application is launched, a startup window SHALL open immediately, and the main VISTA
interface SHALL open only once every service answers. This SHALL hold on every supported
platform. Closing the startup window or the main window, including through the application's Quit
command, SHALL stop every process VISTA started, including code-execution sandboxes started on
behalf of agent sessions. When VISTA is started from its diagnostic terminal launcher instead,
stopping it from that terminal SHALL close the window. When the researcher has turned on
background mode, VISTA's services SHALL instead run as a login service of the researcher's
account: launching the application SHALL open a window onto those services, starting them first
if they are not running, and closing the window or quitting SHALL leave them running. Turning
background mode off SHALL stop the service and restore the behaviour above.

#### Scenario: Startup window opens before services

- **WHEN** the researcher launches VISTA from the Dock, Finder, Spotlight, the Linux app menu or
  the Windows Start menu
- **THEN** a VISTA startup window appears without waiting for the services, and no terminal or
  console window opens

#### Scenario: Window opens when services are ready

- **WHEN** every service answers during startup
- **THEN** the startup window is replaced by one main window showing the VISTA interface at its
  established 1280 × 860 default size, with no browser involved

#### Scenario: Closing during startup stops VISTA

- **WHEN** the researcher closes the startup window or chooses Quit before startup completes
- **THEN** no process VISTA started remains running or holds any of VISTA's ports

#### Scenario: Closing the window stops VISTA

- **WHEN** the researcher closes the main VISTA window, including through the application's Quit
  command
- **THEN** no process VISTA started remains running or holds any of VISTA's ports

#### Scenario: The application ends unexpectedly

- **WHEN** the application process is killed or crashes after starting its launcher
- **THEN** the launcher notices that its parent is gone and stops every process it started

#### Scenario: Interrupting the terminal closes the window

- **WHEN** the researcher presses Ctrl-C in the terminal of a diagnostic launcher they started by
  hand, or closes that terminal
- **THEN** the window closes and no process that launcher started remains running

#### Scenario: Immediate restart

- **WHEN** VISTA is started again immediately after any of these stops
- **THEN** the port preflight reports no conflict

#### Scenario: Closing the window in background mode

- **WHEN** background mode is on and the researcher closes the main window
- **THEN** VISTA's services keep running and the next launch opens a window onto them without
  starting a second set of services

#### Scenario: Launching while the background service runs

- **WHEN** background mode is on, its services are running, and the researcher launches VISTA
- **THEN** the main window opens onto the running services without the startup sequence starting
  any process

#### Scenario: Turning background mode off

- **WHEN** the researcher turns background mode off
- **THEN** the login service is removed and stopped, and closing the window stops VISTA as before
