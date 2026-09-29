## Why

Researchers find out an HPC credential is missing, expired, or for the wrong
project only when a job submission or file fetch fails mid-conversation. The
settings modal can say a token is *saved*, not that it *works*. A spike
(2026-09-25) showed each facility can be asked, cheaply and read-only, whether
it is up and accepts the researcher's token, so the NavRail can show real
per-cluster availability (Junqi's request).

That also exposes a gap: VISTA stores one S3M token, but an S3M token belongs
to one OLCF project, so nobody can be connected to both Odo and Frontier.

## What Changes

- **Per-cluster S3M tokens.** Separate Odo and Frontier tokens replace the
  single `s3m_token`, in storage, settings, and what the backend sends to the
  MCP server. The MCP server stops falling back to the old field. Existing
  tokens are not migrated; researchers paste new ones.
- New **HPC** section in the NavRail with one card per visible cluster
  (Frontier, Odo, Perlmutter): a status dot and status word. Clicking any card
  opens a details popover listing each check, when it last ran, a Recheck
  button, and a link to that cluster's settings.
- A cluster is **Ready** (green) only when live checks pass: the facility is
  up *and* accepted the researcher's token. For Odo and Frontier, a verified
  Globus connection is also required, because without it outputs cannot be
  fetched.
- A new backend endpoint runs the checks for the current user. Checks run when
  the rail mounts, on Recheck, every 5 minutes while visible, and for a single
  cluster right after its credentials are saved.
- **Settings modal rework.** Account and model settings stay at the top; each
  cluster gets its own collapsible section holding a "Show in sidebar" switch,
  its credentials, and its status. A card's Settings link opens the modal with
  only that cluster expanded.

## Capabilities

### New Capabilities
- `hpc-availability`: per-cluster S3M credentials, per-user availability
  checks and the states they resolve to, cluster visibility, and how the
  NavRail and settings modal present them.

### Modified Capabilities
<!-- none: hpc-job-contracts covers CI contracts for job tooling, which this change does not alter -->

## Impact

- **Backend**: new `odo_s3m_token` / `frontier_s3m_token` and
  `hpc_hidden_clusters` columns on `app_user`, with `s3m_token` left in place
  but unused. New `GET /users/me/hpc-status` route. Outbound calls to the IRI
  endpoints, S3M introspect, and Globus. Cluster endpoint settings mirroring
  the MCP server's.
- **MCP server**: `UserConfig` drops the `s3m_token` fallback. Job submission
  uses exactly the per-cluster tokens the cards check.
- **UI**: `NavRail.tsx`, new cluster card / popover components, a reworked
  `UserSettingsModal.tsx`, a new `/api/users/me/hpc-status` proxy route,
  `lib/user.ts`.
- **Upgrade**: saved S3M tokens stop counting; researchers re-paste per cluster (no deployments exist).

## Non-goals

- **Lux** — out until its IRI endpoint exists; adding it is a new cluster entry.
- **Credential expiry beyond S3M.** No estimate of the Globus 3-day session and
  no Perlmutter expiry. Those show only connected / not connected, and the live
  check catches a lapse.
- A NERSC connect flow; Perlmutter keeps its pasted token and stays unverified.
- No job or file-transfer changes beyond which S3M token is used.
- No VISTAGuard work.
