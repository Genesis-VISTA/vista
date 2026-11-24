## Why

Step 3 of the VFS plan is a facility service: researchers use VISTA in a browser, signed in
through Obsidian, without installing anything. The 2026-11-23 scoping chose **one multi-user
VISTA** over a spawner of per-researcher instances (scope doc: "VFS step 3: facility service
scope"). VISTA has been single-user since `single-user-backend` (`301fba76`), so it has no
notion of who a request is from, and the step 2 pilot on Holly stores tokens under a
constant key and echoes them back from `GET /settings`.

## What Changes

- **Hardening first** (both modes): `GET /settings` returns `has_*` flags instead of decrypted
  credentials; a hosted backend refuses to start without a real encryption key; the backend
  and MCP server listen on loopback only and share a secret; `/projects/{name}/mcp/call` is
  off when hosted.
- **A current user on every request.** Desktop: one implicit user, `local`, who owns
  everything that exists today, so a desktop install looks as it did. Hosted: the backend
  verifies the Obsidian Keycloak access token itself (realm `obsidian`, `azp=apisix`, role
  `apisix/vista`) and creates the user on first sign-in.
- **Per-user data.** Projects, chats, runs, jobs, debates, skills, knowledge bases and
  per-user settings (profile, HPC, Globus, repository and inference keys) belong to one user;
  everything else answers `404`. Sharing stays the collaboration server's teams. Server
  settings come from the environment when hosted; there is no admin UI.
- **The hosted flag.** `VISTA_BACKEND_DEPLOYMENT=hosted` is reported to the UI at runtime, so
  one UI build serves both. Hosted: Odo is the only cluster (S3M token checked as open
  enclave on save; the shared Globus pair refused); NERSC, Custom inference, in-app updates,
  Electron-only actions and linked folders are off; skill import is limited to allowed
  hosts; Beam is the server's default model and researchers may add their own MAG or OLCF
  Inference key; sandboxes are capped (4 per user, 40 in all).
- **Collaboration and Slack.** Hosted signs the researcher in to the collaboration server with
  their gateway token. One Slack bot serves the server; a link belongs to the signed-in user
  who redeemed its code.
- **Serving under a path.** A build-time base path, so the UI can live at `/vista/ui` or at
  the root of its own host.
- **Local multi-user testing.** A dev-only token issuer and `./launch.sh --hosted`.
- Tests: tenant isolation across every endpoint (adapted from `301fba76^`).

## Capabilities

### New Capabilities

- `user-accounts`: who a request is from, in each mode; users, server and per-user settings;
  secrets never returned; local multi-user testing.
- `tenant-isolation`: every row, stream, file, background path and agent belongs to one user.
- `hosted-mode`: the deployment flag, what it turns off or limits, and the base path.

### Modified Capabilities

- `hpc-availability`: hosted supports Odo only, with an enclave check on save.
- `inference-providers`: a server-provided model; Custom off when hosted.
- `collaboration-client`: automatic sign-in when hosted.
- `slack-integration`: one bot per server; links owned by users.
- `code-execution-sandbox`: per-user volumes and caps; a scrubbed environment.
- `testing-ci`: the tenant isolation suite.

## Non-goals

- Frontier, Lux and NERSC on hosted, and where their credentials would live (O4, with
  Ross and Lawrence).
- Deploying on Holly and the Obsidian route: change `holly-deployment`.
- Sharing projects inside VISTA, an admin UI, Postgres, and the Electron window as a client
  of the hosted server.
- VISTAGuard.

## Impact

Backend almost everywhere (`api/`, `db/schemas.py`, `services/local_settings.py`, the agent
pool, background tasks, Slack, collaboration client); the UI's proxy routes, settings modal
and every hosted-only control; `vista_mcp_server` (allowed clusters, shared secret);
`scripts/launch.sh`. Schema changes stay additive (nullable `user_id`, backfilled to
`local`), so desktop installs need no reset. Follows `single-user-backend`, which should be
archived first.
