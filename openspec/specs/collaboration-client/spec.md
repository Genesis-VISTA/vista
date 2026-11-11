# collaboration-client Specification

## Purpose
How a VISTA installation finds its collaboration server, tells the researcher whether
publishing and importing can work, and stays fully usable when they cannot.

## Requirements

### Requirement: The server address is a setting

The researcher SHALL be able to set the collaboration server's address in Settings, saved in
the installation's settings as `collaboration_server_url`. It SHALL be an `http://` or
`https://` address, stored without a trailing slash; anything else SHALL be refused with a
message saying what to enter. When it is unset, the installation's configuration
(`VISTA_BACKEND_COLLABORATION_SERVER_URL`) SHALL apply, and the configuration SHALL default to
`https://obsidian.ccs.ornl.gov/vista/api`. That address is VISTA's default server.

#### Scenario: Set and cleared

- **WHEN** the researcher saves `https://collab.example.org/`, then clears the field
- **THEN** the stored address is first `https://collab.example.org`, then null, and the default
  server is in effect again

#### Scenario: A fresh install

- **GIVEN** no address in Settings and no `VISTA_BACKEND_COLLABORATION_SERVER_URL`
- **WHEN** the interface asks for the status
- **THEN** the address in effect is `https://obsidian.ccs.ornl.gov/vista/api`, from the
  configuration

#### Scenario: Not an address

- **WHEN** the researcher saves `collab.example.org` or `http://host:abc`
- **THEN** the save is refused with "enter the server's address, like http://127.0.0.1:8002"

#### Scenario: A malformed configured address

- **WHEN** `VISTA_BACKEND_COLLABORATION_SERVER_URL` is `http://host:abc`
- **THEN** the backend refuses to start, naming the setting

#### Scenario: A blank configured address

- **WHEN** `VISTA_BACKEND_COLLABORATION_SERVER_URL` is set but empty
- **THEN** the backend starts, with no configured server

#### Scenario: Saved only when finished

- **WHEN** the researcher types an address in Settings and pauses
- **THEN** nothing is saved until the field loses focus or the address is pasted

#### Scenario: The researcher's address wins

- **GIVEN** the configuration names one server and the researcher has set another
- **WHEN** the backend contacts the collaboration server
- **THEN** it uses the researcher's, and the status says it came from Settings

### Requirement: Status says whether publishing and importing can work, and why not

`GET /collaboration/status` SHALL return `enabled`, `configured`, `reachable`, `reason`, `url`,
`source`, `server_version`, `signed_in`, `sign_in_required`, `refused`, `shown` and
`default_server`.

While collaboration is off, it SHALL contact no server and SHALL report `enabled` false, not
reachable, `shown` false, and the reason "Collaboration is off. Turn it on under Settings →
Collaboration."

Otherwise it SHALL ask the server's `/version` within a few seconds and SHALL report
`reachable` only for a server that answers with the API version this backend speaks. When the
server cannot be used, `reason` SHALL say why in a sentence naming the address:
- **Not signed in:** "Sign in to <url> to publish and import: get a token from its sign-in page
  and paste it under Settings → Collaboration."
- **Refused (403):** `refused` true, and "<url> refused this sign-in. Signing in again won't help:
  ask the administrators of <url> for access." On the default server, the sentence ends
  "reach out to the VISTA team for access."

`default_server` SHALL be true when the address in effect is VISTA's default server. `shown`
SHALL be true once a collaboration token is saved or the server has signed this installation
in. The check SHALL never fail the request, and no other part of VISTA SHALL depend on the
server.

#### Scenario: No server

- **WHEN** the configuration's address is set but empty and Settings has none
- **THEN** the status is not configured, not reachable, and the reason says to add an address
  under Settings → Collaboration

#### Scenario: Off

- **GIVEN** collaboration is turned off
- **WHEN** the interface asks for the status
- **THEN** no request reaches any server, and the status is not enabled, not shown, with the
  off reason

#### Scenario: Not signed in on the default server

- **GIVEN** the default server answers `401` with no detail
- **WHEN** the interface asks for the status
- **THEN** `sign_in_required` and `default_server` are true, `shown` is false, and the reason is
  the not-signed-in sentence, with no "Unauthorized" in it

#### Scenario: Refused

- **GIVEN** a saved token, and the default server answers `403`
- **WHEN** the interface asks for the status
- **THEN** `refused` and `shown` are true, and the reason ends "reach out to the VISTA team for
  access."

#### Scenario: A server that signs in without a token

- **GIVEN** a development server that trusts the installation's headers, and no saved token
- **WHEN** the interface asks for the status
- **THEN** `signed_in` and `shown` are true

#### Scenario: A server that does not answer

- **GIVEN** an address where nothing is listening
- **WHEN** the interface asks for the status
- **THEN** the answer is `200` with `reachable` false and a reason naming the address

#### Scenario: A server that has moved

- **WHEN** the server answers with a redirect to another address
- **THEN** it is not reachable, and the reason names that address to use instead; no request
  is resent there

#### Scenario: Another API version

- **WHEN** the server reports an API version other than the one this backend speaks
- **THEN** it is not reachable, and the reason names both versions

### Requirement: The backend's client identifies the installation

The backend's client for the collaboration server SHALL send the installation's `install_id`
and display name as the server's identity headers, SHALL report a stale publish as a
version conflict carrying the server's head, and SHALL raise only collaboration errors,
whatever the server answers.

#### Scenario: Published as this installation

- **WHEN** the backend creates an item on the server
- **THEN** the server records the installation's `install_id` and display name as its creator

#### Scenario: A stale publish

- **GIVEN** an item whose head is version 2
- **WHEN** the backend publishes on version 1
- **THEN** it raises a version conflict whose head is 2

#### Scenario: An answer it cannot read

- **WHEN** the server answers a request with `200` and a body of the wrong shape
- **THEN** the client raises a collaboration error saying it did not understand the answer

### Requirement: Publish and import controls are gated with the reason

The interface SHALL offer one shared hook that every collaboration control reads.
- **Hidden:** while the status is not `shown`, which includes while collaboration is off, it
  SHALL report every collaboration control hidden: Publish… and Publish changes, Import, team
  badges, the team filter, Settings' Teams line, and the sharing lines on project, skill and
  knowledge base cards.
- **Disabled:** once shown, it SHALL report them available only when the server is reachable
  and has signed this installation in. Otherwise it SHALL give the reason to show, including
  while the status is being checked or could not be fetched.

Settings SHALL have a Collaboration section, described in "Collaboration can be turned off".
Saving the address SHALL re-check.

#### Scenario: Off while checking

- **WHEN** no status has arrived yet
- **THEN** the hook reports unavailable with "Checking the collaboration server…", and hides
  the controls

#### Scenario: Shown and then expired

- **GIVEN** a saved token the server has since stopped accepting
- **WHEN** the researcher opens the Projects page
- **THEN** each published project still shows its team badge, sharing line and Publish button,
  disabled with the not-signed-in reason

#### Scenario: The address is saved

- **WHEN** the researcher saves a new address in Settings
- **THEN** the answer about the old address is dropped, the controls are off while the new one
  is checked, and every control follows the new answer

#### Scenario: A check that cannot be made

- **GIVEN** an earlier answer about the same address
- **WHEN** a later check fails before reaching the backend
- **THEN** the controls keep the earlier answer, and Settings shows the failure beside it

### Requirement: A local server for development only

`./launch.sh --collab` SHALL start a local collaboration server on `127.0.0.1` beside the
other services and point the backend at it through the configuration, without writing to the
researcher's settings. Both commands SHALL carry these values inline, so they hold in a new
tmux pane or terminal window too. No package, package launcher or Electron startup SHALL start one.

#### Scenario: Launch with a server

- **WHEN** a developer runs `./launch.sh logs --collab`
- **THEN** a collaboration server logs to `logs/collab.log` and the backend's status names it
  with source `config`

### Requirement: Collaboration can be turned off

Settings › Collaboration SHALL begin with a switch labelled "Collaboration", saved as the
installation's `collaboration_enabled`. An installation that has never set it SHALL be on.
- **When off,** the section SHALL show only the switch, the hint "Publish projects, skills and
  knowledge bases to a team on a collaboration server, and import what others have published.
  Nothing leaves this computer until you publish it, choosing its team the first time.", and
  "Off: VISTA doesn't contact a collaboration server, and the Skill Hub, team badges and sharing
  buttons are hidden."
- **When on,** it SHALL also show the address field, the status in words with Check again, and
  the Token field when a sign-in is wanted.
- **Settings list label:** "Off", "Sign in", "Connected" or "No access" (a refusal), with today's
  other labels for the remaining states.
- **Sign-in hint:** on the default server, the Token field's hint SHALL end "No access yet? Reach
  out to the VISTA team."
- **Other surfaces while off:** the Skill Hub entry SHALL be hidden from the sidebar. The Skill
  Hub page, if opened directly, SHALL say collaboration is off with a way to open Settings. Every
  collaboration route that would contact the server SHALL refuse with the off reason, without
  contacting it.
- **Hosted forums while off:** a project whose Hypothesis Lab forum is hosted on the
  collaboration server SHALL have no lab, and nothing SHALL fetch from or push to that forum.
  Its lab page SHALL say "This project's forum is on the collaboration server, and collaboration
  is off." with a way to open Settings. A new project SHALL get no hosted forum. A forum on any
  other git host SHALL be unaffected.

#### Scenario: Turning it off

- **WHEN** the researcher turns the switch off
- **THEN** it is saved, the Skill Hub entry leaves the sidebar, and no further request reaches
  the collaboration server

#### Scenario: Upgrading

- **GIVEN** an installation from before this change, with an address and token saved
- **WHEN** it starts on this version
- **THEN** collaboration is on and everything it showed before is still shown

#### Scenario: A hosted forum while off

- **GIVEN** a project whose forum is hosted on the collaboration server, and collaboration off
- **WHEN** the researcher opens its Hypothesis Lab
- **THEN** no git request reaches the server, and the page says collaboration is off with a
  way to open Settings

#### Scenario: A publish while off

- **GIVEN** collaboration is off
- **WHEN** a client calls a publish or import route
- **THEN** it is refused with the off reason and no request reaches the server

### Requirement: The Skill Hub before sign-in

While collaboration is on and not shown, the sidebar SHALL keep the Skill Hub entry. The Skill
Hub page SHALL show its lede and "Sign in to see what others share. Your own work stays on this
computer until you publish it.", with an Open Settings button that opens Settings ›
Collaboration. On the default server it SHALL add "No access yet? Reach out to the VISTA team."

#### Scenario: A fresh install

- **GIVEN** a fresh install that has not signed in
- **WHEN** the researcher opens the Skill Hub
- **THEN** it shows the sign-in prompt with Open Settings and the VISTA team sentence, and the
  Projects, Skills and Knowledge Bases pages show no Import, Publish, team badge or sharing line
