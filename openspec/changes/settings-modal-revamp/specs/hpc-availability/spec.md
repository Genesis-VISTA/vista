## MODIFIED Requirements

### Requirement: Cluster visibility setting
The researcher SHALL be able to choose which clusters appear in the NavRail.
The choice SHALL persist per user, and all clusters SHALL be shown by default,
including a cluster added after the researcher last changed the setting.
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
- **THEN** the rail no longer shows Lux, and the Lux hub is not probed for that researcher

#### Scenario: All hidden
- **WHEN** every cluster is hidden
- **THEN** the rail's Resources section is not shown

### Requirement: Settings modal organisation
Each supported cluster SHALL have its own section in the settings modal's
resource tree, under its institution and facility: Odo, Frontier and Lux under
ORNL › OLCF, and Perlmutter under LBNL › NERSC. Each cluster's section SHALL
contain:
- its institution and facility, its status dot and word as the rail shows
  them, and its "Show in sidebar" switch;
- all of its settings: for Odo and Frontier, the S3M token, the remote
  directory, and its own Globus connection; for Perlmutter, the NERSC account,
  remote directory, and IRI token; for Lux, the Lux account and remote directory.

The S3M token fields SHALL say that a token from any OLCF project with S3M
access works. The Odo and Frontier remote directory fields SHALL say that VISTA
keeps `<dir>.<user>.jobs` and `<dir>.out` beside the directory, and that the
folder containing them must be writable by the project's group. A Globus
connection SHALL belong to its cluster's section only; there SHALL be no
separate Globus section.

Opened from a cluster's details, the modal SHALL show that cluster's section.
Saving a cluster's credentials, or connecting its Globus account, SHALL trigger
a fresh check of that cluster.

#### Scenario: Deep link from a card
- **WHEN** a researcher opens Frontier's details and follows its Settings link
- **THEN** the settings modal opens showing Frontier's section

#### Scenario: Default open
- **WHEN** a researcher opens settings from the rail's settings button
- **THEN** the modal opens on the Agent section, with every cluster listed in the resource tree and none of their sections shown

#### Scenario: Fix and see it
- **WHEN** a researcher pastes a new Odo token
- **THEN** Odo is rechecked immediately, and its status in the modal and its rail card update without waiting for the next poll

#### Scenario: Globus connected from the cluster
- **WHEN** a researcher connects Globus from Frontier's section
- **THEN** Frontier's Globus check passes on its recheck, and Odo's Globus state is unchanged

#### Scenario: Lux section
- **WHEN** a researcher opens the Lux section
- **THEN** it shows the "Show in sidebar" switch, the Lux account and the Lux remote directory, and no credential fields

#### Scenario: Remote directory saved
- **WHEN** a researcher enters a Frontier remote directory
- **THEN** it is stored for that researcher, and a cleared field is stored as not set

### Requirement: NavRail presentation
The expanded rail SHALL show a Resources section with one card per visible
cluster, grouped under a header naming each cluster's facility (OLCF, NERSC),
in the same facility order as the settings tree. A facility with no visible
cluster SHALL have no header. Each card SHALL show a status dot, whose shape
differs between states as well as its color, and a status word. Clicking any
card, in any state, SHALL open a details view containing:
- each check with its reason;
- for Odo and Frontier, the S3M token's project and planned expiration;
- the settings check, with the remote directory once it is set;
- when the checks ran;
- a Recheck control;
- a link to that cluster's settings.

No other credential expiry SHALL be shown. Hovering a card that is not Ready
SHALL show why, from the message of each check that failed, in both the
expanded and the collapsed rail. The collapsed rail SHALL show each
visible cluster's dot with a short label, grouped under a short facility label,
and an accessible name that includes the state. The rail SHALL run checks on
load, on Recheck, and every 5 minutes while the page is visible. While a check
runs it SHALL keep showing the last known state.

If the status request itself fails, the rail SHALL keep showing the last known
states, noting how long ago they were checked. Once that result is more than
15 minutes old, it SHALL show every visible cluster as Couldn't verify.

#### Scenario: Ready card
- **WHEN** a cluster's state is Ready
- **THEN** its card shows a filled green dot and the word "Ready"

#### Scenario: Grouped by facility
- **WHEN** Odo, Frontier, Lux and Perlmutter are all visible
- **THEN** the rail's Resources section shows Odo, Frontier and Lux under OLCF, then Perlmutter under NERSC

#### Scenario: Facility with nothing visible
- **WHEN** Perlmutter is hidden
- **THEN** the Resources section has no NERSC header

#### Scenario: Details explain a failure
- **WHEN** a researcher opens a card whose state is not Ready
- **THEN** the details show which check failed and why, and link to where it can be fixed

#### Scenario: Not connected still shows the facility
- **WHEN** a researcher opens a Not connected card
- **THEN** the same details view opens, showing the facility's status and the Settings link

#### Scenario: Hover names a missing setting
- **WHEN** a researcher hovers the Odo card and no Odo remote directory is set
- **THEN** the card says "No Odo remote directory is set." without being opened

#### Scenario: Lux details
- **WHEN** a researcher opens the Lux card
- **THEN** the details show the hub's reachability and the sign-in note with the Lux account, with no Globus row and no expiry

#### Scenario: Collapsed rail is labelled
- **WHEN** the rail is collapsed
- **THEN** each cluster's control has an accessible name such as "Frontier: Ready"

#### Scenario: Recheck
- **WHEN** the researcher presses Recheck
- **THEN** a fresh check runs and the card updates without a page reload

#### Scenario: Backend briefly unavailable
- **WHEN** a poll fails and the last successful result is 6 minutes old
- **THEN** the cards keep their last states with a "checked 6 min ago" note

#### Scenario: Backend unavailable for long
- **WHEN** polls have failed and the last successful result is more than 15 minutes old
- **THEN** every visible card shows Couldn't verify

#### Scenario: Hermetic coverage
- **WHEN** PR CI runs
- **THEN** the endpoint, the settings modal, and the rail are tested against faked facility, S3M, Globus, and Lux hub responses with no network access, and any live check is marked `live` and excluded from PR CI
