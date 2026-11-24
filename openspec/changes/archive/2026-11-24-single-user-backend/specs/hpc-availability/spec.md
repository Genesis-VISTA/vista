## MODIFIED Requirements

### Requirement: Cluster visibility setting
The researcher SHALL be able to choose which clusters appear in the NavRail.
The choice SHALL persist in the installation's settings, and all clusters SHALL be shown by
default, including a cluster added after the researcher last changed the setting.
Hiding a cluster SHALL NOT delete or change any of its credentials.

#### Scenario: Hide Perlmutter
- **WHEN** a researcher turns off "Show in sidebar" for Perlmutter
- **THEN** the rail no longer shows Perlmutter, including after a reload, and the saved NERSC IRI token is unchanged

#### Scenario: New user
- **WHEN** a researcher has never changed the setting
- **THEN** Frontier, Odo, Perlmutter, and Lux all appear

#### Scenario: Lux appears for an existing choice
- **WHEN** a researcher hid Perlmutter before Lux was supported
- **THEN** Lux appears and Perlmutter stays hidden

#### Scenario: Hide Lux
- **WHEN** a researcher turns off "Show in sidebar" for Lux
- **THEN** the rail no longer shows Lux, and the Lux host is not probed for that researcher

#### Scenario: All hidden
- **WHEN** every cluster is hidden
- **THEN** the rail's Resources section is not shown
