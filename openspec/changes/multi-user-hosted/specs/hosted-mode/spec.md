## Purpose

One VISTA serves two deployments, a desktop install and a hosted server, and a single flag
decides which. This capability says what that flag changes, and that the server, not the
interface, enforces it.

## ADDED Requirements

### Requirement: One deployment flag, reported at runtime

The deployment SHALL be `desktop` or `hosted`, set by `VISTA_BACKEND_DEPLOYMENT` and defaulting
to `desktop`. The backend SHALL report it through its version route, and the UI SHALL read it
at runtime, so one UI build serves both. Every hosted limit SHALL be enforced by the backend or
MCP server; the UI SHALL only hide what the server refuses.

#### Scenario: The UI follows the backend

- **GIVEN** the same UI build
- **WHEN** it runs against a hosted backend
- **THEN** it shows the sign-out control and hides in-app updates, without being rebuilt

#### Scenario: A hidden control called directly

- **GIVEN** a hosted deployment
- **WHEN** a client calls the update-install route directly
- **THEN** the backend refuses it

### Requirement: What hosted turns off

A hosted deployment SHALL refuse, and the UI SHALL not show: in-app update checks and
installs; Electron-only actions (logs folder, backups, reset, restart); linked folders; NERSC
IRI; the Custom inference provider; and `/projects/{name}/mcp/call`. It SHALL show a sign-out
control and the signed-in user's name, which a desktop install SHALL NOT.

#### Scenario: Direct MCP calls are off

- **GIVEN** a hosted deployment
- **WHEN** a signed-in researcher calls `POST /projects/{name}/mcp/call`
- **THEN** the backend answers `404`

### Requirement: Hosted starts only with real secrets

A hosted backend SHALL refuse to start without a configured encryption key, or with the
development key, or with a key stored inside its data folder. The backend and the VISTA MCP
server SHALL listen on loopback only in both modes, and the MCP server SHALL refuse a request
without the secret it shares with the backend.

#### Scenario: The development key

- **WHEN** a hosted backend starts with no `VISTA_BACKEND_ENCRYPTION_KEY`
- **THEN** it exits with an error naming the setting, before opening the database

#### Scenario: Another process calls the MCP server

- **WHEN** a process other than the backend calls the MCP server without the shared secret
- **THEN** it is refused

### Requirement: Skill import from allowed hosts only

On a hosted deployment, importing a skill from a URL SHALL accept only `https` URLs on a host
in the server's allowed list, and SHALL refuse loopback, link-local and private addresses
whatever the host resolves to. A desktop install keeps accepting any `https` URL.

#### Scenario: An internal address

- **GIVEN** a hosted deployment
- **WHEN** a researcher imports a skill from `https://10.0.0.5/skill.tar.gz`
- **THEN** the import is refused with the reason

### Requirement: The UI can be served under a path

The UI SHALL build with an optional base path (empty by default), so it can be served at
`/vista/ui` or at the root of a host. With a base path every page, asset, API call, stream and
link SHALL resolve under it.

#### Scenario: Served at /vista/ui

- **GIVEN** a UI built with base path `/vista/ui`
- **WHEN** a researcher opens a project and starts a chat
- **THEN** every request the page makes goes to a path under `/vista/ui`
