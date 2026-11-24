## Purpose

Says who every request to the backend is from: the one implicit user on a desktop install, or
the signed-in researcher on a hosted deployment, and which settings are theirs and which are
the server's.

## ADDED Requirements

### Requirement: Every request has a current user

The backend SHALL resolve exactly one current user for every route except its health and
version routes, in one place, before the route runs. On a desktop install the current user
SHALL be the implicit user `local`, created at startup when missing, with no token needed. On
a hosted deployment the current user SHALL come from the access token in the request's
`Authorization: Bearer` header, and a request without a valid one SHALL receive `401`.

#### Scenario: Desktop needs no token

- **GIVEN** a desktop install
- **WHEN** a client calls `GET /projects` with no `Authorization` header
- **THEN** the request is served as the user `local`

#### Scenario: Hosted without a token

- **GIVEN** a hosted deployment
- **WHEN** a client calls `GET /projects` with no token, an expired token, or a token signed by another issuer
- **THEN** it receives `401` and no data

### Requirement: Hosted tokens are verified by the backend

On a hosted deployment the backend SHALL verify each access token itself, whatever the
gateway in front of it has checked: its signature against the issuer's published keys, the
issuer, `typ` `Bearer`, an `azp` among the allowed clients, and the required client role. The
issuer, the allowed clients and the role SHALL be configuration, defaulting to Obsidian's
realm `obsidian`, client `apisix` and role `apisix/vista`. A token without the role SHALL
receive `403`. An unreachable key endpoint SHALL answer `503`, neither admitting nor refusing
the user.

#### Scenario: Signed in without the role

- **GIVEN** a valid token from the right issuer whose user lacks `apisix/vista`
- **WHEN** it is sent to the backend
- **THEN** the backend answers `403`

#### Scenario: An ID token is not an access token

- **WHEN** a request carries a valid ID token (`typ` `ID`) for an allowed user
- **THEN** the backend answers `401`

### Requirement: Users are created on first sign-in

The backend SHALL keep one user per token subject (`sub`), created the first time that
subject makes a verified request, with a display name and email taken from the token and
refreshed when they change. The implicit user `local` SHALL have no subject and SHALL never
match a token.

#### Scenario: First and second request

- **GIVEN** a hosted deployment with no users
- **WHEN** Alice makes two verified requests
- **THEN** exactly one user exists for her subject, and both requests are served as that user

### Requirement: Server settings and user settings

Settings SHALL be split into server settings and user settings. User settings (research
profile, display preferences, the inference provider, model and the researcher's own
inference keys, S3M and Globus credentials, cluster accounts and remote folders, repository
tokens) SHALL belong to one user. Server settings (the server's model endpoint and key, the
collaboration server address, the Slack app, allowed clusters, allowed skill-import hosts,
sandbox limits, the encryption key) SHALL belong to the installation. On a hosted deployment
server settings SHALL come only from the environment and SHALL NOT be writable through the
API; on a desktop install they stay editable in Settings as today.

#### Scenario: Two researchers' keys

- **GIVEN** Alice and Bob on one hosted deployment
- **WHEN** Alice saves an Odo S3M token
- **THEN** Bob's settings show no Odo S3M token

#### Scenario: Server settings are read-only when hosted

- **GIVEN** a hosted deployment
- **WHEN** a signed-in researcher sends a server setting, such as the collaboration server address, in `PUT /settings`
- **THEN** the stored server setting is unchanged and the response says it is set by the server

### Requirement: Settings never return a credential

`GET /settings` and every other response SHALL report each stored credential only as whether
it is set (a `has_*` flag), never its value, in both modes. Saving an empty value SHALL clear
it; omitting it SHALL keep it.

#### Scenario: A saved token is not echoed

- **GIVEN** a researcher who saved an Odo S3M token
- **WHEN** the UI calls `GET /settings`
- **THEN** the response has `has_odo_s3m_token: true` and does not contain the token

### Requirement: Local multi-user testing

The repository SHALL include a development-only token issuer that serves signing keys on
loopback and mints access tokens for a name the developer picks, in the shape Obsidian's
gateway produces, and `./launch.sh --hosted` SHALL start a hosted stack that trusts it. No
package SHALL contain the issuer, and a hosted backend SHALL refuse a loopback issuer unless
it runs in development.

#### Scenario: Two browsers, two researchers

- **GIVEN** `./launch.sh --hosted` running
- **WHEN** a developer signs in as `alice` in one browser and `bob` in another
- **THEN** each sees only their own projects and chats
