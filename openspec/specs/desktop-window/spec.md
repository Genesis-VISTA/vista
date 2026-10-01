# desktop-window Specification

## Purpose
Lets a researcher use VISTA in a dedicated application window whose lifetime is VISTA's
lifetime, instead of a browser tab that can be lost or outlive the services behind it.

## Requirements

### Requirement: The window is VISTA's lifetime

The window SHALL open only once every service answers, and closing it SHALL stop every
process VISTA started, including code-execution sandboxes started on behalf of agent
sessions. Stopping VISTA from the terminal SHALL close the window.

#### Scenario: Window opens when services are ready

- **WHEN** the launcher is run in window mode and all services become healthy
- **THEN** one window opens showing the VISTA interface, with no browser involved

#### Scenario: Closing the window stops VISTA

- **WHEN** the researcher closes the VISTA window, including through the application's
  Quit command
- **THEN** the launcher exits and no process VISTA started remains running or holds any
  of VISTA's ports

#### Scenario: Interrupting the terminal closes the window

- **WHEN** the researcher presses Ctrl-C in the launcher's terminal, or closes that terminal
- **THEN** the window closes and no process VISTA started remains running

#### Scenario: Immediate restart

- **WHEN** VISTA is started again immediately after either kind of stop
- **THEN** the port preflight reports no conflict

### Requirement: One window per installation

Only one VISTA window SHALL run at a time for a given user.

#### Scenario: Second launch while running

- **WHEN** a second window is started while one is already open
- **THEN** the existing window is brought to the front and the second exits

### Requirement: External content opens in the system browser

Links and pop-ups that lead outside VISTA's own origin SHALL open in the system's default
browser. They SHALL NOT open inside the VISTA window or in a VISTA-owned window. The VISTA
window itself SHALL NOT navigate away from VISTA's origin.

#### Scenario: External link

- **WHEN** the researcher follows a link to another site, such as a DOI, a token-source
  page, or a skill repository
- **THEN** it opens in the system browser and the VISTA window stays where it was

#### Scenario: Globus authorization

- **WHEN** the researcher starts connecting Globus from user settings and opens the
  authorization link
- **THEN** the Globus login opens in the system browser, and the code it produces can be
  pasted back into the VISTA window to complete the connection

#### Scenario: Agent-supplied URL

- **WHEN** the researcher confirms opening a URL an agent asked them to visit
- **THEN** it opens in the system browser

#### Scenario: Off-origin navigation is refused

- **WHEN** anything attempts to navigate the VISTA window to another origin, including a
  file dropped outside an upload area
- **THEN** the window stays on its current page

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

### Requirement: Standard desktop editing behaviour

The window SHALL support the platform's standard editing and window shortcuts, including
copy, paste, cut, select-all, undo, and quit.

#### Scenario: Copying and pasting a token

- **WHEN** the researcher copies an API key in another application and pastes it into a
  VISTA settings field with the platform paste shortcut
- **THEN** the key is pasted

### Requirement: The page has no elevated privileges

Content loaded in any VISTA window SHALL have no more capability than it has in a browser
tab: no access to the local filesystem, processes, or application internals beyond what
the web platform grants. Permission requests from the page (camera, microphone,
notifications, geolocation and similar) SHALL be denied.

#### Scenario: Page cannot reach the host

- **WHEN** script in a VISTA page attempts to use host-level APIs
- **THEN** none are available, exactly as in a browser tab

### Requirement: The window in development

The development launcher SHALL open the development stack in the same window by default
in its logs mode, with the interface's live reloading intact, and SHALL offer one option
that starts the services alone, for use in a browser.

#### Scenario: Development window

- **WHEN** a developer starts the development stack in logs mode
- **THEN** the interface opens in the VISTA window once the development server answers,
  edits to the interface reload in that window, and closing it stops the stack

#### Scenario: Services only

- **WHEN** a developer starts the development stack with the services-only option
- **THEN** the services start without a window and the interface is reachable in a
  browser

### Requirement: Window mode where it cannot run

VISTA is a desktop application with no browser mode. Where the window cannot run, the
launcher SHALL say why and stop before starting any service. That covers a package or
platform with no window, a session with no display (including a remote shell session),
running the package as root on Linux, and a Linux host missing system libraries the
window needs. A window that exits with an error SHALL stop VISTA, and the launcher SHALL
report that it stopped and where its log is; the one exception is the start-up retry
without the sandbox on Linux.

#### Scenario: No display

- **WHEN** the launcher is started where no graphical display is available, including a
  remote shell session
- **THEN** it reports that the window cannot be shown and why, and exits without starting
  any service

#### Scenario: Missing window libraries

- **WHEN** the package launcher is started on a Linux host that lacks system libraries the
  window needs
- **THEN** it names the missing libraries and the packages that provide them, and exits
  without starting any service

#### Scenario: The window fails

- **WHEN** a window that started exits with an error, other than at start-up with the
  sandbox on Linux
- **THEN** the launcher reports that the window stopped unexpectedly, its exit status and
  where its log is, and stops every service

#### Scenario: Closing the window still stops VISTA

- **WHEN** the researcher closes a window that started successfully
- **THEN** VISTA stops, as it does when the window is closed at any other time

### Requirement: The renderer sandbox wherever the host permits it

Every VISTA window SHALL run its pages inside the operating system's renderer sandbox
whenever the host permits it. The window SHALL run without that sandbox only where the
host's own security policy prevents the sandbox from starting for an application
installed the way VISTA is. Whenever the window runs without it, the launcher SHALL say
so on every start and, where VISTA ships one, SHALL name the one-time step that allows the
sandbox on that host.
The sandbox SHALL NOT be turned off on macOS. Because a check before start-up cannot
tell every blocked host from a permitted one (an AppArmor profile that is installed but
not loaded looks the same), a window on Linux that started with the sandbox and exits
with an error within its first seconds SHALL be started once more without it, and the
launcher SHALL say that it did so and where the first attempt's log is.

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

#### Scenario: A host that blocks user namespaces some other way

- **WHEN** VISTA opens its window on a Linux host that does not allow unprivileged user
  namespaces for another reason, such as a container's seccomp policy or
  `user.max_user_namespaces=0`
- **THEN** the window opens without the renderer sandbox, and the launcher reports that
  it is running without the sandbox and why, naming no install step

#### Scenario: A profile installed but not loaded

- **WHEN** VISTA's AppArmor profile file is present but not loaded, and the window aborts
  at start-up because its sandbox cannot start
- **THEN** the launcher reports that the window stopped at start, starts it again without
  the sandbox, and names the log of the first attempt

#### Scenario: Development window

- **WHEN** a developer opens the development stack in the window on any of these hosts
- **THEN** the sandbox is decided by the same check, and its reason appears in the
  window's output and log
