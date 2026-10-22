## Purpose

One VISTA installation serves one researcher. Their settings and credentials live in a single
local settings row that every part of VISTA reads, with no login and no identity per request.

## ADDED Requirements

### Requirement: One settings row per installation

The backend SHALL keep the researcher's settings and credentials in exactly one local
settings row, created at startup when missing. No request SHALL need an identity: there are
no users, no administrators and no project members. Credentials SHALL stay encrypted at rest
and write-only tokens SHALL NOT be returned.

#### Scenario: First start

- **WHEN** the backend starts with an empty database
- **THEN** one settings row exists, and `GET /settings` returns it

#### Scenario: A second start keeps it

- **GIVEN** settings saved on an earlier run
- **WHEN** the backend restarts
- **THEN** the same settings row, with the same values, is returned and no second row exists

#### Scenario: No identity needed

- **WHEN** a client calls any route without an identity header
- **THEN** the route answers as it would for the installation's researcher

### Requirement: The installation has an identity of its own

The settings row SHALL carry an `install_id`, a UUID generated once when the row is created
and never changed by any update, and an editable `display_name`.

#### Scenario: The install id is stable

- **GIVEN** an installation that has started once
- **WHEN** it restarts, or the researcher saves other settings
- **THEN** `install_id` is unchanged

#### Scenario: The install id cannot be set

- **WHEN** a client sends an `install_id` in an update
- **THEN** the stored `install_id` is unchanged

### Requirement: Settings are served under `/settings`

The backend SHALL serve the settings as `GET` and `PUT /settings`, the inference view as
`GET /settings/inference`, cluster status as `GET /settings/hpc-status`, and Globus
connection as `POST /settings/globus/{cluster}/login` and `/code`. The `/users` routes SHALL
NOT exist.

#### Scenario: The old route is gone

- **WHEN** a client calls `GET /users/me`
- **THEN** it receives `404`
