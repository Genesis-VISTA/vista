## MODIFIED Requirements

### Requirement: One settings row per installation

The backend SHALL keep the installation's server settings in exactly one local settings row,
created at startup when missing, and each user's settings in a row of their own (see
`user-accounts`). On a desktop install there SHALL be exactly one user, the implicit user
`local`, whose settings are the researcher's, so no request needs an identity. Credentials
SHALL stay encrypted at rest and SHALL NOT be returned by any route.

#### Scenario: First start

- **WHEN** the backend starts with an empty database
- **THEN** one settings row and the user `local` exist, and `GET /settings` returns the merged settings without credential values

#### Scenario: A second start keeps it

- **GIVEN** settings saved on an earlier run
- **WHEN** the backend restarts
- **THEN** the same settings, with the same values, are returned and no second settings row or `local` user exists

#### Scenario: No identity needed

- **GIVEN** a desktop install
- **WHEN** a client calls any route without an identity header
- **THEN** the route answers as it would for the user `local`

#### Scenario: Settings from before this change

- **GIVEN** a desktop settings row from before user settings existed
- **WHEN** the new version starts
- **THEN** the researcher's fields are copied into `local`'s user settings, and every field shows the value it had
