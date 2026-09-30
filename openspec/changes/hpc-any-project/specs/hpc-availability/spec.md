# Spec Delta

## MODIFIED Requirements

### Requirement: Per-cluster S3M credentials
The system SHALL store a separate S3M token for Odo and for Frontier. Each
field SHALL accept a token from any OLCF project, including two different
projects for the two clusters. Job submission and file operations for each cluster SHALL use only
that cluster's token. The legacy single S3M token SHALL NOT be read, shown, or
sent anywhere, and SHALL NOT be migrated into either cluster's field.

#### Scenario: Both clusters connected
- **WHEN** a researcher saves an Odo token minted in one project and a Frontier token minted in another
- **THEN** jobs can be submitted to both clusters, each using its own token and charged to its own token's project

#### Scenario: Legacy token ignored
- **WHEN** a researcher has only the legacy single S3M token saved
- **THEN** Odo and Frontier are Not connected, and job submission to either reports that no token is configured for that cluster

#### Scenario: Clearing one token
- **WHEN** a researcher clears the Frontier token and saves
- **THEN** the Odo token and Odo job submission are unaffected

### Requirement: Credential check
For each cluster with a saved credential, the system SHALL verify it with an
authenticated, read-only call to the cluster's IRI service that the facility
refuses for an invalid token. Merely having a credential saved SHALL NOT count
as passing. For Odo and Frontier the system SHALL also introspect the S3M
token, and SHALL report the token's project and planned expiration. It SHALL
NOT compare the token's project with any configured account. An answer that
is neither success nor 401 SHALL be reported as unverifiable, not as rejected.

For Lux, the credential check SHALL always pass without any call. It SHALL say
that the researcher signs in with a PIN and RSA passcode when a chat first uses
Lux. It SHALL NOT name a project. No outcome of a chat's sign-in SHALL change
it.

#### Scenario: Token accepted
- **WHEN** the IRI service answers the authenticated call successfully
- **THEN** the credential check passes and, for Odo/Frontier, reports the token's project and planned expiration

#### Scenario: Token for another project
- **WHEN** an Odo S3M token is valid and belongs to a project VISTA has no configuration for
- **THEN** the credential check passes, reporting that project, and there is no Wrong project outcome

#### Scenario: No credential saved
- **WHEN** the researcher has no token for the cluster
- **THEN** the cluster is Not connected, and no authenticated call is made

#### Scenario: Token rejected
- **WHEN** the IRI service or S3M introspection answers 401
- **THEN** the credential check fails as rejected

#### Scenario: Token not yet active
- **WHEN** introspection reports a delayed start whose time is still in the future
- **THEN** the credential check fails as rejected, with the reason "not active until" that time

#### Scenario: Unexpected answer
- **WHEN** the IRI service answers with a status other than success or 401
- **THEN** the credential check is unverifiable, and reports that HTTP status

#### Scenario: Lux sign-in is described, not checked
- **WHEN** any researcher's Lux status is checked
- **THEN** Lux's credential check passes, says sign-in happens in a chat with a PIN and RSA passcode, names no project, and makes no outbound call

#### Scenario: A failed chat sign-in does not reject Lux
- **WHEN** a researcher's Lux sign-in failed in a chat and the hub still answers
- **THEN** Lux is Ready

### Requirement: Globus check for OLCF clusters
For Odo and Frontier the system SHALL verify the Globus connection the
cluster's file operations would use. It SHALL choose the same credential
source, in the same order, that those operations choose. Only credentials the
researcher connected count; there is no deployment-wide credential. It SHALL
verify the connection with a live call to the cluster's collection, so that an
expired High Assurance session is detected rather than reported as connected.
It SHALL NOT estimate when a session will lapse.

#### Scenario: Globus verified
- **WHEN** the researcher has a complete Globus credential for the cluster and a read-only call to its collection succeeds
- **THEN** the Globus check passes

#### Scenario: Globus missing or incomplete
- **WHEN** none of the researcher's sources holds both the Transfer and the collection token for the cluster
- **THEN** the Globus check fails as Not connected

#### Scenario: Deployment credential does not count
- **WHEN** the researcher has connected no Globus account and `VISTA_MCP_ODO_GLOBUS_REFRESH_TOKEN` and `VISTA_MCP_ODO_GLOBUS_HTTPS_REFRESH_TOKEN` are set in the environment
- **THEN** Odo's Globus check fails as Not connected

#### Scenario: Globus session expired
- **WHEN** the collection answers 401
- **THEN** the Globus check fails as Session expired, distinct from Not connected

### Requirement: Cluster state
The system SHALL resolve each cluster to exactly one state, by taking the first
that applies in this order: Checking, Degraded, Couldn't verify, Not connected,
Token rejected, Globus not connected, Globus session expired, Ready. A cluster
SHALL be Ready only when every check that applies to it passes. For Odo and
Frontier that includes the Globus check. Lux SHALL have no state of its own; it
uses the same order.

#### Scenario: Odo without Globus
- **WHEN** Odo's facility and credential checks pass but the Globus check fails
- **THEN** Odo's state is Globus not connected (or Globus session expired), not Ready

#### Scenario: Perlmutter ready
- **WHEN** Perlmutter's facility and credential checks pass
- **THEN** Perlmutter's state is Ready, with no Globus check applied

#### Scenario: Lux ready
- **WHEN** the Lux hub answers the probe
- **THEN** Lux's state is Ready, with no Globus check applied

#### Scenario: Facility down outranks credentials
- **WHEN** a cluster's facility check fails and its credential check also fails
- **THEN** its state is Degraded

#### Scenario: Unverifiable credential
- **WHEN** the facility is up and the credential check is unverifiable
- **THEN** the state is Couldn't verify, neither Ready nor Token rejected

### Requirement: Settings modal organisation
The settings modal SHALL keep account and model settings at the top, always
visible, followed by one collapsible section per supported cluster. Each
cluster's section SHALL contain:
- its "Show in sidebar" switch;
- all of its settings: for Odo and Frontier, the S3M token, the remote
  directory, and the Globus connection; for Perlmutter, the NERSC account,
  remote directory, and IRI token; for Lux, the remote directory;
- in its header, the same status dot and word the rail shows.

The S3M token fields SHALL say that a token from any OLCF project with S3M
access works. The Odo and Frontier remote directory fields SHALL say that the
directory must be writable by the project's group.

Opened from the rail's settings button, every cluster section SHALL start
collapsed. Opened from a cluster's details, only that cluster's section SHALL
be expanded. Saving a cluster's credentials, or connecting its Globus account,
SHALL trigger a fresh check of that cluster.

#### Scenario: Deep link from a card
- **WHEN** a researcher opens Frontier's details and follows its Settings link
- **THEN** the settings modal opens with only the Frontier section expanded

#### Scenario: Default open
- **WHEN** a researcher opens settings from the rail's settings button
- **THEN** all cluster sections are collapsed

#### Scenario: Fix and see it
- **WHEN** a researcher pastes a new Odo token and saves
- **THEN** Odo is rechecked immediately, and its section header and rail card update without waiting for the next poll

#### Scenario: Lux section
- **WHEN** a researcher expands the Lux section
- **THEN** it shows the "Show in sidebar" switch and the Lux remote directory, and no credential fields

#### Scenario: Remote directory saved
- **WHEN** a researcher enters a Frontier remote directory and saves
- **THEN** it is stored for that researcher, and a cleared field is stored as not set

### Requirement: NavRail presentation
The expanded rail SHALL show an HPC section with one card per visible cluster.
Each card SHALL show a status dot, whose shape differs between states as well
as its color, and a status word. Clicking any card, in any state, SHALL open a
details view containing:
- each check with its reason;
- for Odo and Frontier, the S3M token's project and planned expiration;
- when the checks ran;
- a Recheck control;
- a link to that cluster's settings.

No other credential expiry SHALL be shown. The collapsed rail SHALL show each
visible cluster's dot with a short label, and an accessible name that includes
the state. The rail SHALL run checks on load, on Recheck, and every 5 minutes
while the page is visible. While a check runs it SHALL keep showing the last
known state.

If the status request itself fails, the rail SHALL keep showing the last known
states, noting how long ago they were checked. Once that result is more than
15 minutes old, it SHALL show every visible cluster as Couldn't verify.

#### Scenario: Ready card
- **WHEN** a cluster's state is Ready
- **THEN** its card shows a filled green dot and the word "Ready"

#### Scenario: Details explain a failure
- **WHEN** a researcher opens a card whose state is not Ready
- **THEN** the details show which check failed and why, and link to where it can be fixed

#### Scenario: Not connected still shows the facility
- **WHEN** a researcher opens a Not connected card
- **THEN** the same details view opens, showing the facility's status and the Settings link

#### Scenario: Lux details
- **WHEN** a researcher opens the Lux card
- **THEN** the details show the hub's reachability and the sign-in note, with no project, no Globus row and no expiry

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
