## Purpose

One server, the backend, serves both VISTA's prebuilt UI and its API on a single
origin. That way the window, file links and the API never cross an origin, and
no separate UI server runs.

## ADDED Requirements

### Requirement: The backend serves the UI and the API on one origin

The backend SHALL answer both VISTA's UI pages and its API on one origin. Every
API endpoint SHALL live under the `/api` path prefix, and nothing outside `/api`
SHALL be an API endpoint. Each UI page SHALL be served at its own route (`/`,
`/projects`, `/skills`, `/skill-hub`, `/datasets`, `/hypothesis-lab`,
`/knowledge-bases`, `/knowledge-bases/project`), with or without a trailing
slash, together with the static assets those pages load. No other process SHALL
be needed to serve the UI.

#### Scenario: A page and an API call share an origin
- **WHEN** the window opens the backend's URL and the page fetches `/api/projects`
- **THEN** the page and the project list come from the same origin, and no request goes to any other port

#### Scenario: A page route that shares a name with an API resource
- **WHEN** `GET /skills` and `GET /api/skills` are requested
- **THEN** the first returns the skills page as HTML and the second returns the skills list as JSON

#### Scenario: A deep page link is loaded directly
- **WHEN** `/knowledge-bases/project` is requested directly, for example by reloading the window on that page
- **THEN** the backend returns that page, not the index page and not a 404

### Requirement: Unknown paths answer in the shape of their space

A request for a path under `/api` that matches no endpoint SHALL return a 404
with a JSON body. A request for any other path that matches no page or asset
SHALL return a 404 with the UI's not-found page as HTML.

#### Scenario: Unknown API path
- **WHEN** `GET /api/no-such-thing` is requested
- **THEN** the response is 404 with `content-type: application/json`

#### Scenario: Unknown page path
- **WHEN** `GET /no-such-page` is requested while the UI is being served
- **THEN** the response is 404 with the UI's not-found page as HTML

### Requirement: The UI folder is configured, and its absence is not fatal

The backend SHALL take the location of the prebuilt UI from a setting. When the
setting is unset, or names a folder that is missing or contains no UI index
page, the backend SHALL still start and serve `/api`, SHALL answer every
non-`/api` path with 404, and SHALL log once at startup that it is serving the
API only, naming the reason (setting unset, or folder not found or not a UI
build, with its path).

#### Scenario: Backend run without a UI build
- **WHEN** the backend starts with the UI-folder setting unset
- **THEN** `/api` endpoints work, `GET /` returns 404, and the startup log says the UI is not being served because no UI folder is configured

#### Scenario: Configured folder is missing
- **WHEN** the UI-folder setting names a path that does not exist
- **THEN** the backend starts, serves `/api` only, and the startup log names that path as missing

### Requirement: UI files are served without login, the API is not

The UI's pages and static assets SHALL be served without the login check that
guards the API, because they contain no user data. Every `/api` endpoint SHALL
keep requiring it exactly as before this change.

#### Scenario: Pages load before any API call
- **WHEN** `GET /` and its script and style assets are requested with no user identity
- **THEN** they are served, and `GET /api/users/me` under the same conditions behaves exactly as the user endpoint did before this change

### Requirement: Default ports are fixed and uncommon

The backend, whose address is the app's origin, SHALL listen on port 38417 by
default, and the vista MCP server on port 38418. The same defaults SHALL apply
in development and in packages. `VISTA_BACKEND_PORT` and `VISTA_MCP_PORT` SHALL
override them. No setting for a separate UI port SHALL exist. The port SHALL NOT
change between launches unless an override changes it, so that state the
browser keeps per origin survives restarts.

#### Scenario: Default launch
- **WHEN** VISTA starts with no port overrides
- **THEN** the backend and the window's origin are `http://127.0.0.1:38417`, and the backend reaches the vista MCP server at port 38418

#### Scenario: Override
- **WHEN** `VISTA_BACKEND_PORT=40000` is set
- **THEN** the backend and the window's origin use port 40000

### Requirement: Development keeps a hot-reloading UI on the same paths

In development, the UI SHALL be served by a hot-reloading dev server, and
requests from its pages to `/api/...` SHALL reach the backend's `/api/...`
unchanged and through the page's own origin, so UI code uses identical paths in
development and in packages. Streaming responses (chat runs, run events, debate
events) SHALL arrive incrementally through this path, not only once the stream
ends.

#### Scenario: Dev page calls the API
- **WHEN** a page served by the dev server fetches `/api/projects`
- **THEN** the backend's project list is returned through the dev server's origin

#### Scenario: Dev streaming is incremental
- **WHEN** a chat run streams in development
- **THEN** its events appear in the page as the backend emits them

### Requirement: Agent file links address the backend's file endpoints

File links the agent produces for project outputs and uploads (for example a
plot shown with `display_file`) SHALL be same-origin paths of the form
`/api/projects/{name}/{outputs|uploads}/{path}`, and SHALL open inside the
window like any other same-origin link. Links in the previous form
(`/api/files/{kind}/{path}?project_name=...`) SHALL NOT be rewritten or
redirected; links of that form already saved in chat history stop resolving.

#### Scenario: Plot from a new chat
- **WHEN** the agent displays a file it wrote to the project's outputs
- **THEN** the chat shows it from `/api/projects/{name}/outputs/{path}` on the window's origin

#### Scenario: Link saved before the change
- **WHEN** a chat saved before this change contains `/api/files/outputs/plot.png?project_name=X`
- **THEN** that link returns 404, and nothing redirects it
