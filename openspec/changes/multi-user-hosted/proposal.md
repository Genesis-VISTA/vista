## Why

VFS step 3 is a facility service: researchers use VISTA in a browser, signed in through
Obsidian. Scoping on 2026-11-23 chose **one multi-user VISTA** over per-researcher instances
(scope doc "VFS step 3: facility service scope"). Since `single-user-backend` VISTA has no
notion of who a request is from, and the Holly pilot stores tokens under a constant key and
echoes them from `GET /settings`.

## What Changes

- **Hardening first**, both modes: credentials reported as `has_*` flags only; hosted refuses
  to start without a real encryption key; backend and MCP server on loopback with a shared
  secret; `/projects/{name}/mcp/call` off when hosted.
- **A current user on every request.** Desktop: the implicit user `local`, owning everything
  that exists today. Hosted: the backend verifies the Obsidian access token itself (realm
  `obsidian`, `azp=apisix`, role `apisix/vista`) and creates the user on first sign-in.
- **Per-user data and settings.** Everything a researcher makes, and their credentials, belong
  to them; anything else answers `404`. Sharing stays the collaboration server's teams.
  Hosted server settings come from the environment; no admin UI.
- **The hosted flag**, read by the UI at runtime. Hosted: Odo only (S3M token checked as open
  enclave; shared Globus pair refused); NERSC, Custom inference, in-app updates,
  Electron-only actions and linked folders off; skill import from allowed hosts; Beam as the
  server model, with the researcher's own MAG or OLCF key optional; sandboxes capped at 4 per
  user and 40 in all.
- **Collaboration and Slack**: hosted signs researchers in to the collaboration server with
  their gateway token; one Slack bot per server, each link owned by a user.
- A build-time base path (`/vista/ui` or a host's root), a dev-only token issuer with
  `./launch.sh --hosted`, and a tenant isolation suite.

## Capabilities

### New Capabilities

- `user-accounts`: who a request is from; users; server and user settings; no secrets
  returned; local multi-user testing.
- `tenant-isolation`: rows, streams, files, background work and agents belong to one user.
- `hosted-mode`: the deployment flag, what it limits, and the base path.

### Modified Capabilities

- `local-settings`: the settings row holds server settings; users have their own.
- `hpc-availability`: hosted supports allowed clusters only, with an enclave check.
- `inference-providers`: a server-provided model; Custom off when hosted.
- `collaboration-client`: automatic sign-in when hosted.
- `slack-integration`: one app per server; links owned by users.
- `code-execution-sandbox`: per-user sandboxes, caps, a scrubbed environment.
- `testing-ci`: the tenant isolation suite.

## Non-goals

- Frontier, Lux and NERSC on hosted and their credential storage (O4).
- Deploying on Holly and the Obsidian route: change `holly-deployment`.
- In-VISTA sharing, an admin UI, Postgres, the Electron window as a hosted client.
- VISTAGuard.

## Impact

Backend routers, schemas, settings, agent pool, background tasks, Slack and collaboration
client; the UI proxy, settings modal and hosted-only controls; `vista_mcp_server`;
`scripts/launch.sh`. Schema changes are additive (nullable `user_id` backfilled to `local`), so
desktop installs need no reset. Builds on the archived `single-user-backend`.
