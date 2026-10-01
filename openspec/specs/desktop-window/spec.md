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
window. Files that VISTA offers for download SHALL be saved to a location the researcher
chooses.

#### Scenario: Opening a cited paper

- **WHEN** the researcher opens a PDF from a knowledge base
- **THEN** it opens in a separate VISTA window that can be closed without affecting the
  main window

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

A developer SHALL be able to run the development stack in the same window, with the
interface's live reloading intact, using one option on the development launcher.

#### Scenario: Development window

- **WHEN** a developer starts the development stack with the window option
- **THEN** the interface opens in the VISTA window once the development server answers,
  edits to the interface reload in that window, and closing it stops the stack

### Requirement: Window mode where it cannot run

Where the window cannot run, the launcher SHALL say so and fall back to browser mode,
rather than failing after services have started. That covers a platform whose artifact
has no window, and a session with no display.

#### Scenario: No display

- **WHEN** the launcher is started in window mode where no graphical display is available
- **THEN** it reports that the window cannot be shown, reports the address, and keeps the
  services running as in browser mode
