# hpc-availability Specification

## Purpose
Show each researcher, per HPC cluster, whether VISTA can actually use that
cluster for them right now — verified by live, read-only calls to the facility
rather than inferred from which credentials happen to be saved.

## Requirements

### Requirement: Per-cluster S3M credentials
The system SHALL store a separate S3M token for Odo and for Frontier. Job
submission and file operations for each cluster SHALL use only that cluster's
token. The legacy single S3M token SHALL NOT be read, shown, or sent anywhere,
and SHALL NOT be migrated into either cluster's field.

#### Scenario: Both clusters connected
- **WHEN** a researcher saves an Odo token for Odo's project and a Frontier token for Frontier's project
- **THEN** jobs can be submitted to both clusters, each using its own token

#### Scenario: Legacy token ignored
- **WHEN** a researcher has only the legacy single S3M token saved
- **THEN** Odo and Frontier are Not connected, and job submission to either reports that no token is configured for that cluster

#### Scenario: Clearing one token
- **WHEN** a researcher clears the Frontier token and saves
- **THEN** the Odo token and Odo job submission are unaffected

### Requirement: Supported clusters
The system SHALL report availability for exactly these clusters: Frontier,
Odo, Perlmutter, and Lux. Frontier and Odo SHALL be checked with that cluster's
S3M token and Globus connection. Perlmutter SHALL be checked with the
researcher's NERSC IRI token. Lux SHALL be checked without any credential,
because a researcher signs in to it interactively from a chat and nothing is
stored.

#### Scenario: Cluster list
- **WHEN** a signed-in researcher with no clusters hidden requests HPC status
- **THEN** the response contains one entry each for Frontier, Odo, Perlmutter, and Lux, and no others

### Requirement: Facility check
For Frontier, Odo, and Perlmutter, the system SHALL determine whether the
facility reports the cluster's compute resource as up, using the facility's
resource status list. It SHALL treat the facility as degraded when the resource
is not up, or when an incident naming it is currently open. An incident that
has ended SHALL NOT affect the result.

For Lux, which is in no facility status list, the system SHALL instead open a
connection to the configured Lux hub's SSH port and wait for its SSH greeting,
within the check timeout. It SHALL then send its own SSH identification line,
naming itself as a VISTA status probe, and close the connection. It SHALL NOT
begin key exchange or authenticate. Any failure SHALL be reported as unreachable, never as
degraded, because the probe cannot tell an outage from a network path that
does not reach the hub. The Lux login node behind the hub SHALL NOT be probed.

The facility check SHALL NOT require any credential.

#### Scenario: Facility up, no open incidents
- **WHEN** the facility lists the cluster's compute resource as `up` and no open incident names it
- **THEN** the facility check passes

#### Scenario: Resolved incident is ignored
- **WHEN** the only incident naming the resource has an end time in the past
- **THEN** the facility check passes

#### Scenario: Facility reports down or maintenance
- **WHEN** the resource is listed as anything other than `up`, or an open incident names it
- **THEN** the facility check fails with the facility's status and, when present, the incident's name and time window

#### Scenario: Facility unreachable
- **WHEN** the facility's status endpoint does not answer within the check timeout
- **THEN** the facility check fails as unreachable, and the cluster is not reported Ready

#### Scenario: Lux hub answers
- **WHEN** the Lux hub accepts the connection and sends a line beginning `SSH-` within the timeout
- **THEN** Lux's facility check passes, and names the hub it reached

#### Scenario: Lux hub does not answer
- **WHEN** the hub's name does not resolve, the connection is refused or times out, or the first line is not an SSH greeting
- **THEN** Lux's facility check fails as unreachable, and Lux is Couldn't verify, not Degraded

#### Scenario: Lux probe is shared
- **WHEN** two researchers request status within the result reuse window
- **THEN** the Lux hub is probed once, unless one of them requests a fresh check

### Requirement: Credential check
For each cluster with a saved credential, the system SHALL verify it with an
authenticated, read-only call to the cluster's IRI service that the facility
refuses for an invalid token. Merely having a credential saved SHALL NOT count
as passing. For Odo and Frontier the system SHALL also introspect the S3M
token: it SHALL confirm the token's project matches the cluster's configured
account, and SHALL report the token's planned expiration. An answer that is
neither success nor 401 SHALL be reported as unverifiable, not as rejected.

For Lux, the credential check SHALL always pass without any call. It SHALL say
that the researcher signs in with a PIN and RSA passcode when a chat first uses
Lux, and SHALL name the configured project Lux jobs run under. No outcome of a
chat's sign-in SHALL change it.

#### Scenario: Token accepted
- **WHEN** the IRI service answers the authenticated call successfully and, for Odo/Frontier, the token's project matches
- **THEN** the credential check passes and, for Odo/Frontier, reports the token's planned expiration

#### Scenario: No credential saved
- **WHEN** the researcher has no token for the cluster
- **THEN** the cluster is Not connected, and no authenticated call is made

#### Scenario: Token rejected
- **WHEN** the IRI service or S3M introspection answers 401
- **THEN** the credential check fails as rejected

#### Scenario: Token not yet active
- **WHEN** introspection reports a delayed start whose time is still in the future
- **THEN** the credential check fails as rejected, with the reason "not active until" that time

#### Scenario: Token for another project
- **WHEN** the S3M token is valid but its project is not the cluster's account
- **THEN** the credential check fails as Wrong project, naming the expected project

#### Scenario: Unexpected answer
- **WHEN** the IRI service answers with a status other than success or 401
- **THEN** the credential check is unverifiable, and reports that HTTP status

#### Scenario: Lux sign-in is described, not checked
- **WHEN** any researcher's Lux status is checked
- **THEN** Lux's credential check passes, names the configured project, says sign-in happens in a chat with a PIN and RSA passcode, and makes no outbound call

#### Scenario: A failed chat sign-in does not reject Lux
- **WHEN** a researcher's Lux sign-in failed in a chat and the hub still answers
- **THEN** Lux is Ready

### Requirement: Globus check for OLCF clusters
For Odo and Frontier the system SHALL verify the Globus connection the
cluster's file operations would use. It SHALL choose the same credential
source, in the same order, that those operations choose, and a deployment-wide
credential counts. It SHALL verify the connection with a live call to the
cluster's collection, so that an expired High Assurance session is detected
rather than reported as connected. It SHALL NOT estimate when a session will
lapse.

#### Scenario: Globus verified
- **WHEN** a complete Globus credential exists for the cluster and a read-only call to its collection succeeds
- **THEN** the Globus check passes and reports whether it used the researcher's own identity or the deployment's

#### Scenario: Globus missing or incomplete
- **WHEN** no source holds both the Transfer and the collection token for the cluster
- **THEN** the Globus check fails as Not connected

#### Scenario: Globus session expired
- **WHEN** the collection answers 401
- **THEN** the Globus check fails as Session expired, distinct from Not connected

### Requirement: Cluster state
The system SHALL resolve each cluster to exactly one state, by taking the first
that applies in this order: Checking, Degraded, Couldn't verify, Not connected,
Token rejected, Wrong project, Globus not connected, Globus session expired,
Ready. A cluster SHALL be Ready only when every check that applies to it
passes. For Odo and Frontier that includes the Globus check. Lux SHALL have no
state of its own; it uses the same order.

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

### Requirement: Status endpoint
The backend SHALL expose the current researcher's per-cluster status at an
authenticated endpoint. Each entry SHALL include:
- the state;
- the result of each individual check, with a short human-readable reason;
- the time the checks ran.

The response SHALL NOT include any token value or token-derived secret. The
endpoint SHALL check clusters concurrently and bound each outbound call with a
timeout. It SHALL reuse a recent result for a short time, unless the caller
asks for a fresh check of all clusters or of one named cluster.

#### Scenario: No secrets in the response
- **WHEN** the endpoint returns any result, including errors from a facility
- **THEN** the body contains no token, refresh token, or authorization header value

#### Scenario: Recheck bypasses the cache
- **WHEN** the caller requests a fresh check
- **THEN** all checks run again, even though a cached result exists

#### Scenario: Single-cluster recheck
- **WHEN** the caller requests a fresh check of one cluster
- **THEN** only that cluster's checks run again, and the other entries come from the cache

#### Scenario: Hidden clusters are not checked
- **WHEN** a cluster is hidden in the researcher's settings
- **THEN** no outbound call is made for it

### Requirement: Cluster visibility setting
The researcher SHALL be able to choose which clusters appear in the NavRail.
The choice SHALL persist per user, and all clusters SHALL be shown by default,
including a cluster added after the researcher last changed the setting.
Hiding a cluster SHALL NOT delete or change any of its credentials.

#### Scenario: Hide Perlmutter
- **WHEN** a researcher turns off "Show in sidebar" for Perlmutter and saves
- **THEN** the rail no longer shows Perlmutter, including after a reload, and the saved NERSC IRI token is unchanged

#### Scenario: New user
- **WHEN** a researcher has never changed the setting
- **THEN** Frontier, Odo, Perlmutter, and Lux all appear

#### Scenario: Lux appears for an existing choice
- **WHEN** a researcher hid Perlmutter before Lux was supported
- **THEN** Lux appears and Perlmutter stays hidden

#### Scenario: Hide Lux
- **WHEN** a researcher turns off "Show in sidebar" for Lux and saves
- **THEN** the rail no longer shows Lux, and the Lux hub is not probed for that researcher

#### Scenario: All hidden
- **WHEN** every cluster is hidden
- **THEN** the rail's HPC section is not shown

### Requirement: Settings modal organisation
The settings modal SHALL keep account and model settings at the top, always
visible, followed by one collapsible section per supported cluster. Each
cluster's section SHALL contain:
- its "Show in sidebar" switch;
- all of its credentials: for Odo and Frontier, the S3M token and Globus
  connection; for Perlmutter, the NERSC account, remote directory, and IRI
  token; Lux has none, so its section SHALL contain only its switch;
- in its header, the same status dot and word the rail shows.

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
- **THEN** it shows only the "Show in sidebar" switch, with no credential fields

### Requirement: NavRail presentation
The expanded rail SHALL show an HPC section with one card per visible cluster.
Each card SHALL show a status dot, whose shape differs between states as well
as its color, and a status word. Clicking any card, in any state, SHALL open a
details view containing:
- each check with its reason;
- for Odo and Frontier, the S3M token's planned expiration;
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
- **THEN** the details show the hub's reachability and the sign-in note with the project, with no Globus row and no expiry

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
