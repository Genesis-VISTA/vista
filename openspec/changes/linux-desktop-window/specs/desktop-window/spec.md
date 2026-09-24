## ADDED Requirements

### Requirement: The renderer sandbox wherever the host permits it

Every VISTA window SHALL run its pages inside the operating system's renderer sandbox
whenever the host permits it. The window SHALL run without that sandbox only where the
host's own security policy prevents the sandbox from starting for an application
installed the way VISTA is. Whenever the window runs without it, the launcher SHALL say
so on every start and SHALL name the one-time step that allows the sandbox on that host.
The sandbox SHALL NOT be turned off on a host that permits it, and SHALL NOT be turned
off on macOS.

#### Scenario: A host that permits the sandbox

- **WHEN** VISTA opens its window on a Linux host whose policy allows the sandbox, such
  as Debian 13, Fedora or RHEL 10
- **THEN** the window runs with the renderer sandbox on and the launcher reports nothing
  about it

#### Scenario: Ubuntu restricting user namespaces

- **WHEN** VISTA opens its window on an Ubuntu host that restricts unprivileged user
  namespaces and VISTA's AppArmor profile is not installed
- **THEN** the window opens without the renderer sandbox, and the launcher reports that
  it is running without the sandbox, why, and the command that installs the profile

#### Scenario: The profile installed

- **WHEN** VISTA opens its window on the same Ubuntu host after the profile has been
  installed
- **THEN** the window runs with the renderer sandbox on and the launcher reports nothing
  about it

#### Scenario: Development window

- **WHEN** a developer opens the development stack in the window on any of these hosts
- **THEN** the sandbox is decided in the same way and reported in the same way

## MODIFIED Requirements

### Requirement: VISTA's own documents and files are usable without tabs

Content that VISTA serves and that a browser would show in a new tab SHALL open in a VISTA
window. The exception is PDFs while the window runs without the renderer sandbox: those
SHALL open in the system browser instead. Files that VISTA offers for download SHALL be
saved to a location the researcher chooses.

#### Scenario: Opening a cited paper

- **WHEN** the researcher opens a PDF from a knowledge base in a window that runs with the
  renderer sandbox
- **THEN** it opens in a separate VISTA window that can be closed without affecting the
  main window

#### Scenario: Opening a cited paper without the sandbox

- **WHEN** the researcher opens a PDF from a knowledge base in a window that runs without
  the renderer sandbox
- **THEN** it opens in the system browser and the VISTA window stays where it was

#### Scenario: Downloading a file

- **WHEN** the researcher downloads a dataset file or an agent-produced file
- **THEN** a save dialog appears and the file is written where they choose

### Requirement: Window mode where it cannot run

Where the window cannot run, the launcher SHALL say so and fall back to browser mode,
rather than failing after services have started. That covers a platform whose artifact
has no window, a session with no display, a host missing system libraries the window
needs, and a window that fails. A window that exits with an error, at start-up or later,
SHALL NOT stop VISTA's services; only closing or quitting it does.

#### Scenario: No display

- **WHEN** the launcher is started in window mode where no graphical display is available,
  including a remote shell session
- **THEN** it reports that the window cannot be shown, reports the address, and keeps the
  services running as in browser mode

#### Scenario: Missing window libraries

- **WHEN** the launcher is started in window mode on a host that lacks system libraries
  the window needs
- **THEN** it names the missing libraries, reports the address, and keeps the services
  running as in browser mode

#### Scenario: The window fails

- **WHEN** the window exits with an error, such as a crash at start-up because its
  sandbox cannot start
- **THEN** the launcher reports that the window failed and where its log is, reports the
  address, and keeps the services running as in browser mode until it is interrupted

#### Scenario: Closing the window still stops VISTA

- **WHEN** the researcher closes a window that started successfully
- **THEN** VISTA stops, as it does when the window is closed at any other time
