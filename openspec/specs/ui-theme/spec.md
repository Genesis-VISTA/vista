# ui-theme Specification

## Purpose
Defines how VISTA picks its light or dark appearance, follows the operating system by default,
lets a researcher override that on their machine, and keeps both themes legible.

## Requirements

### Requirement: Appearance follows the operating system by default

With no stored preference, VISTA SHALL render in the operating system's current light or dark
appearance, and SHALL switch when the OS appearance changes while VISTA is open, without a
reload.

#### Scenario: First launch on a dark OS

- **WHEN** a researcher who has never chosen an appearance opens VISTA while their OS is set to
  dark
- **THEN** VISTA renders in the dark theme

#### Scenario: OS switches while VISTA is open

- **WHEN** the appearance setting is System and the OS changes from light to dark (for example
  macOS Auto at sunset)
- **THEN** VISTA changes to the dark theme without a reload and without losing the open
  conversation or any unsent input

### Requirement: Researcher can override the appearance

VISTA SHALL offer an Appearance setting with exactly three choices: System, Light and Dark.
Light and Dark SHALL apply regardless of the OS appearance. System SHALL restore the default
behavior. The setting SHALL show which choice is active, and with System it SHALL state that it
matches the computer's setting.

#### Scenario: Forcing dark on a light OS

- **WHEN** a researcher whose OS is light selects Dark
- **THEN** VISTA switches to the dark theme immediately and stays dark if the OS appearance
  changes

#### Scenario: Returning to System

- **WHEN** a researcher who had selected Dark selects System while their OS is light
- **THEN** VISTA switches to the light theme and follows the OS again from then on

### Requirement: The choice persists on that machine

The appearance choice SHALL persist across page reloads, browser restarts and VISTA window
restarts on the same machine and browser profile. It SHALL apply to every open VISTA tab or
window on that machine. It is a per-machine display preference and SHALL NOT be stored with
the researcher's account or sent to the backend.

#### Scenario: Choice survives a restart

- **WHEN** a researcher selects Light, quits VISTA, and opens it again
- **THEN** VISTA opens in the light theme regardless of the OS appearance

#### Scenario: Two windows stay in step

- **WHEN** two VISTA tabs are open and the researcher changes the appearance in one
- **THEN** the other tab adopts the same appearance without a reload

#### Scenario: Unavailable browser storage

- **WHEN** the browser refuses access to local storage (for example a locked-down profile)
- **THEN** VISTA still renders, follows the OS appearance, and applies an in-session choice
  until the page is reloaded

### Requirement: No flash of the wrong theme

VISTA SHALL paint its first frame in the resolved appearance. A researcher in dark mode SHALL
NOT see a light frame while the page or the VISTA window loads, and the reverse SHALL hold for
light mode.

#### Scenario: Loading in dark mode

- **WHEN** a researcher whose resolved appearance is dark loads or reloads any VISTA page
- **THEN** the page background is dark from the first painted frame

#### Scenario: Opening the VISTA window on a dark OS

- **WHEN** the VISTA window opens while the OS is dark
- **THEN** the window's own background is dark before the page has loaded

### Requirement: Both themes are legible

In both themes, body text, secondary text, links, button labels, status text (success,
warning, danger) and terminal output SHALL meet a contrast ratio of at least 4.5:1 against the
surface they are drawn on. Interactive control boundaries and filled buttons SHALL meet at
least 3:1 against their surroundings. Native form controls and scrollbars SHALL follow the
active theme.

#### Scenario: Status pills in dark mode

- **WHEN** an HPC cluster card shows connected, queued and failed states in the dark theme
- **THEN** each state's text meets 4.5:1 against its pill background, and the three states
  remain distinguishable from each other

#### Scenario: Form controls in dark mode

- **WHEN** a researcher opens the settings modal in the dark theme
- **THEN** text inputs, selects, checkboxes and scrollbars render in dark styling, not as light
  native widgets

### Requirement: The light theme is unchanged

Introducing the dark theme SHALL NOT change how VISTA looks in the light theme.

#### Scenario: Light theme screenshots

- **WHEN** the existing UI screenshot suite runs in the light theme after this change
- **THEN** the screenshots match those taken before it, apart from the new Appearance section
  in the settings modal

### Requirement: Agent-produced content keeps its own ground

HTML cards rendered from agent output, and raster images such as plots, SHALL keep a light
background inside the dark theme, and SHALL NOT be color-inverted or filtered. The frame around
them SHALL follow the active theme.

#### Scenario: Plot in dark mode

- **WHEN** an agent displays a matplotlib PNG with a white background while VISTA is dark
- **THEN** the plot shows with its original colors on a light mat inside a dark-themed card

#### Scenario: Sandboxed HTML in dark mode

- **WHEN** an agent's HTML output renders in a sandboxed card while VISTA is dark
- **THEN** the card's content keeps a white page background and the card border uses the dark
  theme's colors

### Requirement: Terminal surfaces stay dark in both themes

Raw agent output and the log viewer SHALL render as dark console surfaces in both themes, and
SHALL remain visually distinct from the cards around them in the dark theme.

#### Scenario: Tool output in dark mode

- **WHEN** a tool call's raw output is shown in a chat card while VISTA is dark
- **THEN** the output area is darker than the card surrounding it and reads as a separate
  console
