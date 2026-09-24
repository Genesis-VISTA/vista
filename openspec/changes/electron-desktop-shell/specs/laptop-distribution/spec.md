## MODIFIED Requirements

### Requirement: Single command to install and run

A single command SHALL perform any outstanding first-run setup and then start VISTA. That
command SHALL be safe to re-run, and on a subsequent run SHALL skip work already done. On
a platform whose artifact includes the application window, the command SHALL end by
opening VISTA in that window; an explicit browser-mode option SHALL instead report the
address to open, as on platforms without a window.

#### Scenario: First run

- **WHEN** the command is run for the first time after unpacking
- **THEN** it prepares the sandbox image, creates the state directory, installs the bundled
  corpus, seeds the database, starts the services, and opens the VISTA window (or, in
  browser mode or on a platform without a window, reports the address to open)

#### Scenario: Subsequent run

- **WHEN** the command is run again on an already-initialised installation
- **THEN** it starts the services without repeating setup and without re-importing the
  sandbox image

#### Scenario: Browser mode

- **WHEN** the command is run with the browser-mode option on a platform that has a window
- **THEN** no window opens, the address to open is reported, and the services run until
  the command is interrupted, exactly as on a platform without a window
