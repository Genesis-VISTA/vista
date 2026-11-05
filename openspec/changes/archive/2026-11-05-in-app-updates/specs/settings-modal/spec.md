## ADDED Requirements

### Requirement: About section

The settings navigation SHALL end with an About section. About SHALL show the installed
version and commit, each copyable, a link to that version's release notes, and the update
state: when checks are off or not run for this build, why; otherwise the time of the last
check, a Check now action, and the newer release when there is one with its notes link. About
SHALL hold the two update settings: check for updates, and include prereleases. Like every
other setting, they SHALL save as they are changed.

#### Scenario: Reading the version

- **WHEN** a researcher opens About on 0.2.3
- **THEN** it shows version 0.2.3 and its commit, each with a copy button

#### Scenario: An update is available

- **WHEN** 0.2.5 has been found and the researcher opens About
- **THEN** it shows that 0.2.5 is available with its release notes link and an Update action

#### Scenario: Turning checks off

- **WHEN** the researcher turns off check for updates
- **THEN** the setting saves at once and About says update checks are off

### Requirement: Settings marks an available update

While a newer release is available, the rail's settings button SHALL carry a small marker,
and About's navigation entry SHALL carry the same marker. Nothing else in the interface SHALL
announce the update, and the marker SHALL NOT be dismissible other than by updating or turning
checks off.

#### Scenario: Marker shown

- **WHEN** the backend reports 0.2.5 as available
- **THEN** the settings button and the About entry each show the marker, and no banner or
  toast appears

#### Scenario: Marker cleared

- **WHEN** the researcher turns update checks off
- **THEN** the marker disappears
