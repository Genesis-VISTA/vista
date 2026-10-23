## Purpose

How a VISTA installation finds its collaboration server, tells the researcher whether
publishing and importing can work, and stays fully usable when they cannot.

## ADDED Requirements

### Requirement: The server address is a setting

The researcher SHALL be able to set the collaboration server's address in Settings, saved in
the installation's settings as `collaboration_server_url`. It SHALL be an `http://` or
`https://` address, stored without a trailing slash; anything else SHALL be refused with a
message saying what to enter. When it is unset, the installation's configuration
(`VISTA_BACKEND_COLLABORATION_SERVER_URL`) SHALL apply.

#### Scenario: Set and cleared

- **WHEN** the researcher saves `https://collab.example.org/`, then clears the field
- **THEN** the stored address is first `https://collab.example.org`, then null

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

`GET /collaboration/status` SHALL return `configured`, `reachable`, `reason`, `url`,
`source` and `server_version`. It SHALL ask the server's `/version` within a few seconds and
SHALL report `reachable` only for a server that answers with the API version this backend
speaks. Otherwise `reason` SHALL say why in a sentence naming the address. The check SHALL
never fail the request, and no other part of VISTA SHALL depend on the server.

#### Scenario: No server

- **WHEN** no address is set in Settings or the configuration
- **THEN** the status is not configured, not reachable, and the reason says to add an address
  under Settings → Collaboration

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

The interface SHALL offer one shared hook that every Publish and Import control reads: it
SHALL report them available only once the status says the server is reachable, and
otherwise SHALL give the reason to show, including while the status is being checked or
could not be fetched. Settings SHALL have a Collaboration section showing the address field,
the status in words, and a way to check again; saving the address SHALL re-check.

#### Scenario: Off while checking

- **WHEN** no status has arrived yet
- **THEN** the hook reports unavailable with "Checking the collaboration server…"

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
