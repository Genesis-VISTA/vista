## ADDED Requirements

### Requirement: Opened at a named section

Settings SHALL be able to be opened by parts of the application other than the rail's
settings button, at a section that part names. It SHALL then show that section's settings,
including in a narrow window, where the section is shown in place of the navigation list.
Opening Settings this way SHALL NOT change how it opens from the rail's settings button.

#### Scenario: Opened by the tour at the model provider

- **WHEN** the guided tour opens Settings at the model provider section
- **THEN** the model provider section is shown and selected in the navigation

#### Scenario: Narrow window

- **WHEN** Settings is opened at the model provider section in a half-width window
- **THEN** the model provider section is shown rather than the navigation list

### Requirement: Tour replay and documentation

The Research profile section SHALL offer **Replay tutorial**, which closes Settings and starts
the guided tour from its welcome step, and a **Documentation** link to
<https://genesis-vista.github.io/>, which opens in the system browser.

#### Scenario: Replaying the tour

- **WHEN** a researcher chooses **Replay tutorial** in Settings
- **THEN** Settings closes and the tour opens at the welcome step

#### Scenario: Opening the documentation

- **WHEN** a researcher chooses **Documentation** in Settings
- **THEN** VISTA's documentation opens in the system browser, and VISTA stays as it was
