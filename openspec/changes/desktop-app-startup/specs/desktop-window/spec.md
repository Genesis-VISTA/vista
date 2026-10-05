## MODIFIED Requirements

### Requirement: The window is VISTA's lifetime

When the application is launched, a startup window SHALL open immediately, and the main VISTA
interface SHALL open only once every service answers. This SHALL hold on every supported
platform. Closing the startup window or the main window, including through the application's Quit
command, SHALL stop every process VISTA started, including code-execution sandboxes started on
behalf of agent sessions. When VISTA is started from its diagnostic terminal launcher instead,
stopping it from that terminal SHALL close the window.

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

### Requirement: One window per installation

Only one VISTA application instance SHALL start services at a time for a given user. A second
launch SHALL focus whichever window the existing instance is showing, and SHALL start no
launcher or service.

#### Scenario: Second launch during startup

- **WHEN** VISTA is launched again while the first instance shows its startup window
- **THEN** the startup window is brought to the front and no second launcher or service process
  is started

#### Scenario: Second launch while running

- **WHEN** VISTA is launched again while the main window is open
- **THEN** the main window is brought to the front and the second launch exits without starting a
  second service stack

### Requirement: Window mode where it cannot run

VISTA is a desktop application with no browser mode. Where the window cannot run, VISTA SHALL say
why and stop before starting any service. That covers a package or platform with no window, a
session with no display (including a remote shell session), running the package as root on Linux,
and a Linux host missing system libraries the window needs. Where the application is started
without a terminal and no window can open, the reason SHALL be shown as a desktop notification
where the host offers one, and SHALL always be written to VISTA's window log. A window that exits
with an error SHALL stop VISTA, and VISTA SHALL report that it stopped and where its log is; the
one exception is the start-up retry without the sandbox on Linux.

#### Scenario: No display

- **WHEN** the diagnostic launcher is started where no graphical display is available, including
  a remote shell session
- **THEN** it reports that the window cannot be shown and why, and exits without starting any
  service

#### Scenario: Missing window libraries from the app menu

- **WHEN** VISTA is launched from the Linux app menu on a host that lacks system libraries the
  window needs
- **THEN** a desktop notification names the missing libraries and the packages that provide them,
  the same text is written to the window log, and no service is started

#### Scenario: Missing window libraries

- **WHEN** the diagnostic launcher is started on a Linux host that lacks system libraries the
  window needs
- **THEN** it names the missing libraries and the packages that provide them, and exits without
  starting any service

#### Scenario: The window fails

- **WHEN** a window that started exits with an error, other than at start-up with the sandbox on
  Linux
- **THEN** VISTA reports that the window stopped unexpectedly, its exit status and where its log
  is, and stops every service

#### Scenario: Closing the window still stops VISTA

- **WHEN** the researcher closes a window that started successfully
- **THEN** VISTA stops, as it does when the window is closed at any other time

### Requirement: The renderer sandbox wherever the host permits it

Every VISTA window SHALL run its pages inside the operating system's renderer sandbox whenever the
host permits it. The window SHALL run without that sandbox only where the host's own security
policy prevents the sandbox from starting for an application installed the way VISTA is. Whenever
the window runs without it, VISTA SHALL say so on every start and, where VISTA ships one, SHALL
name the one-time step that allows the sandbox on that host. On Linux this decision SHALL be made
before the window's process starts, whether VISTA is launched from the app menu or from the
diagnostic launcher.
The sandbox SHALL NOT be turned off on macOS. Because a check before start-up cannot tell every
blocked host from a permitted one (an AppArmor profile that is installed but not loaded looks the
same), a window on Linux that started with the sandbox and exits with an error within its first
seconds SHALL be started once more without it. VISTA SHALL say that it did so and where the first
attempt's log is.

#### Scenario: A host that permits the sandbox

- **WHEN** VISTA opens its window on a Linux host whose policy allows the sandbox, such as Debian
  13, Fedora or RHEL 10
- **THEN** the window runs with the renderer sandbox on and VISTA reports nothing about it

#### Scenario: Ubuntu restricting user namespaces

- **WHEN** VISTA opens its window on an Ubuntu host that restricts unprivileged user namespaces
  and VISTA's AppArmor profile is not installed
- **THEN** the window opens without the renderer sandbox, and VISTA reports that it is running
  without the sandbox, why, and the command that installs the profile

#### Scenario: The profile installed

- **WHEN** VISTA opens its window on the same Ubuntu host after the profile has been installed
- **THEN** the window runs with the renderer sandbox on and VISTA reports nothing about it

#### Scenario: A host that blocks user namespaces some other way

- **WHEN** VISTA opens its window on a Linux host that does not allow unprivileged user
  namespaces for another reason, such as a container's seccomp policy or
  `user.max_user_namespaces=0`
- **THEN** the window opens without the renderer sandbox, and VISTA reports that it is running
  without the sandbox and why, naming no install step

#### Scenario: A profile installed but not loaded

- **WHEN** VISTA's AppArmor profile file is present but not loaded, and the window aborts at
  start-up because its sandbox cannot start
- **THEN** VISTA reports that the window stopped at start, starts it again without the sandbox,
  and names the log of the first attempt

#### Scenario: Launched from the app menu on a restricting host

- **WHEN** VISTA is launched from the Linux app menu on a host that requires running without the
  sandbox
- **THEN** the window starts without it from the first attempt, and the startup window says that
  it is running without the sandbox and why

#### Scenario: Development window

- **WHEN** a developer opens the development stack in the window on any of these hosts
- **THEN** the sandbox is decided by the same check, and its reason appears in the window's
  output and log

## ADDED Requirements

### Requirement: Startup reports real activity

The startup window SHALL report the actual initialization phase it receives from a versioned,
machine-readable launcher protocol, which every platform's launcher SHALL emit identically. It
SHALL NOT derive state by parsing human log messages, and SHALL NOT show a percentage or a
remaining-time estimate.

#### Scenario: First-run activity

- **WHEN** bundled resources or the sandbox image need first-run preparation
- **THEN** the startup window identifies the active preparation step and marks it complete only
  after the launcher reports completion

#### Scenario: Subsequent startup skips completed work

- **WHEN** a resource is already installed or an image is already imported
- **THEN** the activity is reported as completed or skipped without repeating the work

#### Scenario: Same events on every platform

- **WHEN** the same package version starts on macOS, Linux and Windows
- **THEN** each platform's launcher reports the same phases, states and error codes, in the same
  order

#### Scenario: Unsupported protocol

- **WHEN** the application receives a startup protocol version it does not support
- **THEN** startup stops with an application-version error instead of guessing how to interpret
  the events

### Requirement: Startup failures are actionable in the application

An expected startup failure SHALL remain in the startup window and identify the failed activity,
a concise cause and the available remedy. The researcher SHALL be able to open the log location,
retry after cleanup, or quit. Diagnostics offered for copying SHALL exclude credentials and
environment values.

#### Scenario: A service does not become healthy

- **WHEN** a VISTA service fails or exceeds its startup health timeout
- **THEN** the startup window names that service, offers its log location, and offers Retry and
  Quit without opening a terminal

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

### Requirement: Startup privileges do not reach the VISTA page

The local startup renderer SHALL receive only the capabilities needed to observe startup and to
invoke its fixed controls. The main VISTA page SHALL continue to have no preload bridge, local
filesystem, process or application-internal access.

#### Scenario: Transition to the main interface

- **WHEN** the startup renderer is replaced by the main VISTA interface
- **THEN** none of the startup renderer's launcher, log, clipboard or retry capabilities are
  exposed to the main VISTA page

### Requirement: VISTA is listed and recognised by the desktop

Each platform's install SHALL make VISTA launchable from that platform's usual place, with VISTA's
icon. On macOS that place SHALL be Finder, Spotlight, Launchpad and the Dock; on Linux, the
desktop's app menu; on Windows, the Start menu. A running VISTA window SHALL be shown with VISTA's
icon, grouped with that entry.

#### Scenario: macOS

- **WHEN** VISTA has been installed on macOS
- **THEN** Spotlight finds `VISTA`, opening it starts VISTA, and its Dock icon is VISTA's

#### Scenario: Linux app menu

- **WHEN** VISTA has been installed on a Linux desktop
- **THEN** the app menu lists VISTA with its icon, choosing it starts VISTA without a terminal,
  and the running window is grouped under that entry

#### Scenario: Windows Start menu

- **WHEN** VISTA has been installed on Windows
- **THEN** the Start menu lists VISTA with its icon, and choosing it starts VISTA without a
  console window
