## Context

See proposal.md for motivation and specs/hpc-availability/spec.md for the
required behavior. This section covers only what shapes the approach.

- **Tokens live in the backend.** `app_user` holds them Fernet-encrypted, and
  the backend passes them to the MCP server per tool call in `_meta.vista.user`.
  MCP tools are reached only through a *project* agent
  (`api/mcp.py` → `project_agent_pool`). The rail is global and shows with no
  project open.
- **The backend already talks to Globus**, in `services/globus_auth.py`, and
  knows each cluster's collection id. The IRI URLs, OLCF accounts, and Odo's
  pinned compute resource id live only in the MCP server's `config.py`.
- **S3M tokens today.** The DB and UI store one `s3m_token`. The MCP server's
  `UserConfig` already has `odo_s3m_token` and `frontier_s3m_token` fields,
  which nothing populates. `require_s3m_token` falls back to `s3m_token`
  (`lib/user_config.py:30-38`). `submit_job_mcp.py:240-255` decides which
  clusters are enabled from whichever of the three is present.
- **No deployments exist to migrate.** Researchers re-paste tokens.
- **Spike results (2026-09-25)**, probed against the live endpoints:

  | Call | Auth | Result |
  |---|---|---|
  | `GET {iri}/api/v1/status/resources` | none | 200, 50–250 ms, all three facilities; each row has `name`, `group`, `current_status` |
  | `GET {iri}/api/v1/status/resources/{id}` | none | OLCF says `unknown` while the list says `up`: **unusable** |
  | `GET {iri}/api/v1/status/incidents` | none | 200; includes long-resolved incidents, each with `start`/`end`/`resolution` |
  | `GET {iri}/api/v1/compute/resources` | S3M (Odo) | **200 with a valid token, 401 with a bad one**, ~100 ms |
  | `GET {iri}/api/v1/account/*` | S3M | 401 with a valid token too: **can't tell good from bad** |
  | `GET {iri}/api/v1/compute/status/{rid}/{fake}` | S3M | 502: the lookup reaches the scheduler. **Don't use** |
  | S3M introspect | S3M | 200 → `token.project`, `plannedExpiration`, `securityEnclave`, `delayedStart`, `delayDate`; 401 when bad |

  - `plannedExpiration` and `delayDate` are ISO-8601 UTC with microseconds
    (OLCF S3M docs), e.g. `2024-11-08T14:45:38.756330Z`.
  - Frontier is assumed to behave like Odo for `compute/resources`, and will
    be verified once Frontier access works.
  - Perlmutter is assumed the same and stays unverified. The researcher
    pastes its token (an opaque Globus access token), and VISTA can't learn
    when it expires.

## Goals / Non-Goals

**Goals:**
- One backend round trip gives the rail everything it draws.
- Checks are cheap enough to run on every rail mount.
- The cards and job submission use exactly the same credentials, so green
  means submission will authenticate.
- The state logic is a pure function, so tests don't need a network or clock.

**Non-Goals:**
- Routing checks through the MCP server.
- Pushing status updates (websocket/SSE). The rail polls.
- Estimating Globus session lapse, or any expiry beyond S3M's `plannedExpiration`.
- Any admin or aggregate view across users.

## Decisions

### 1. Per-cluster S3M tokens, legacy field ignored
- There are new encrypted columns `odo_s3m_token` and `frontier_s3m_token`
  on `app_user`, created by `db.py`'s auto-add-column path. They go in
  `_USER_CONFIG_NULLABLE_FIELDS` so a blank input stores null.
- The backend sends them in the MCP metadata under those same names, which
  `UserConfig` already accepts. It stops sending `s3m_token`.
- `s3m_token` stays in the table, because SQLite column drops aren't worth it
  here. It's removed from every schema the API exposes and from the UI.
- In the MCP server, `UserConfig` loses its `s3m_token` field and fallback,
  and `submit_job_mcp.py` stops treating it as enabling either cluster.
- *Alternative, migrate by introspecting each token's project:* rejected. There
  are no deployments, and pasting a new token is trivial.

### 2. Checks run in the backend, not the MCP server
A new `services/hpc_status.py` makes the outbound calls with `httpx` and
`globus_sdk`. `api/users.py` exposes
`GET /users/me/hpc-status?fresh=<bool>&cluster=<name>`.
- *Alternative, an MCP tool:* this would reuse `iri.py` and `globus.py`, but it
  needs a project agent to call. It would also put a UI-only check on the
  agent's tool list, which it has no reason to see.
- *Cost:* the IRI URLs, OLCF accounts, and resource names are duplicated from
  the MCP config. Mitigation: the backend reads the **same `VISTA_MCP_*` env
  var names**, so one override moves both. A hermetic test also asserts the
  backend defaults equal the MCP server's defaults by parsing
  `mcp_servers/vista_mcp_server/src/vista_mcp_server/config.py`.

### 3. Which call proves what
| Check | Call | Pass | Fail |
|---|---|---|---|
| Facility | public `status/resources` + `status/incidents` | row `current_status == "up"` and no incident naming it has `end` unset or in the future | `degraded` / `unreachable` |
| Credential (all) | `GET compute/resources` with the token | 200 | 401 → `rejected`; any other status → `unverifiable` |
| Credential (Odo/Frontier) | S3M introspect | 200, `project == <account>`, not delayed into the future; report `plannedExpiration` | 401 → `rejected`; project mismatch → `wrong_project`; future `delayDate` → `rejected` with "not active until" |
| Globus (Odo/Frontier) | refresh both tokens; Transfer `operation_ls` of the collection home, limit 1 | refresh and ls both succeed | none complete → `not_connected`; 401 / `ConsentRequired` → `session_expired` |

- The facility check always uses the public feed. It then behaves the same
  whether or not the researcher is connected.
- Resource rows are matched by name (`Odo`, `Frontier`), and for NERSC by
  `group == "perlmutter"`, `name == "compute"`, the same match `iri.py` uses.
- The Globus credential source follows the order in
  `UserConfig.require_globus_token`: the cluster's own pair, then the shared
  pair, then the `VISTA_MCP_{ODO,FRONTIER}_GLOBUS_*` deployment pair. It's
  reimplemented as a small pure function, because the MCP module isn't
  importable from the backend.

### 4. State resolution is a pure function
`resolve_state(facility, credential, globus | None) -> State` applies the
spec's precedence: Checking, Degraded, Couldn't verify, Not connected, Token
rejected, Wrong project, Globus not connected, Globus session expired, Ready.
- Each check returns `{ok, reason, detail}`, where `reason` is a short enum
  and `detail` is non-secret context: an HTTP status, an incident name, the
  expected project, the S3M expiry.
- The UI maps reasons to copy.
- Every spec scenario becomes one row of a table-driven test.

### 5. Concurrency, timeouts, caching
- All clusters and checks run under `asyncio.gather`. Each outbound call has a
  5 s timeout, and a timeout counts as `unreachable` or `unverifiable`
  depending on the check.
- The per-user result is cached in-process for 60 s per cluster. The key is
  the user id, the cluster, and a hash of that cluster's credential columns,
  so saving a new credential invalidates it by itself.
- `fresh=true` bypasses the cache for all clusters, and `fresh=true&cluster=x`
  bypasses it for one.
- S3M introspect has its own 10 min cache, as in `olcf_token.py`. The public
  facility feed is cached 60 s per facility across users.

### 6. Visibility stored as *hidden* clusters
There's a new nullable JSON column, `app_user.hpc_hidden_clusters: list[str]`,
defaulting to empty. It's exposed through `GET/PUT /users/me`, which rejects
unknown names. Storing what's hidden means a cluster added later (Lux) shows by
default. The status endpoint skips hidden clusters entirely.

### 7. UI

**The approved design is the canvas "HPC Availability Cards",
<https://claude.ai/artifact/MaA5z6cFb8a37jMt7TiZ9x>, version `1790342674-d0f8`.**
Build to it:
- **Main** (expanded rail with Frontier's details open): the section's
  placement and label, the card anatomy, and the popover's structure.
- **Collapsed** (collapsed rail on hover): the dot plus two-letter label, and
  the tooltip.
- **States**: the dot shape and color per state, and the status-word styling.

It uses the app's own tokens (`globals.css`), so spacing, radii and colors map
directly. The canvas predates the grilling, so where it disagrees with the
spec, **the spec wins**:
- **Lux** is drawn but out of scope. Omit it.
- The **"Ready · files not connected"** row (a green ring) contradicts the
  spec. Odo and Frontier without Globus are **Globus not connected**, not a
  kind of Ready. Draw that state in the not-ready family, not green.
- **Token expired** is **Token rejected** in the spec. Use the spec's word,
  with "Not active until …" as its delayed-start reason.
- **Not drawn yet:**
  - Couldn't verify
  - Wrong project
  - Globus not connected
  - Globus session expired
  - the "checked N min ago" stale note
  - the settings modal rework

  Derive these from the States board's pattern: a distinct dot shape per
  state, a muted status word, and red text only for things the researcher
  must fix.
- The popover's rows follow Decision 3's checks. Where the canvas has
  "Facility is up" and "S3M token accepted" as separate rows, keep both. The
  S3M row shows the project and planned expiration; the Globus row shows
  which identity is in use.

Update the canvas before or during group 4, so the review screenshots in
task 6.2 have a current reference.

- `ui/lib/hpc-status.ts` has a `useHpcStatus()` hook, a single shared store so
  the rail and the settings modal see the same data. It fetches on mount,
  polls every 5 min while `document.visibilityState === "visible"`, and
  exposes `recheck(cluster?)`.
- On a failed fetch it keeps the last result plus its `checkedAt`. The rail
  shows "checked N min ago", and past 15 min maps every card to Couldn't verify.
- `ui/components/HpcStatusSection.tsx` renders the cards, the details popover,
  and the collapsed-rail items. It's mounted in `NavRail.tsx` under Opened
  Project. The dot shapes follow the canvas (filled / ring / hollow / triangle
  / dashed / ring-with-question-mark), using the `--success`, `--danger`,
  `--warning`, and `--muted` tokens.
- **Settings modal rework.** `UserSettingsModal.tsx` keeps identity and the
  model fields at the top, then one collapsible section per cluster built
  from a shared cluster list:
  - The header shows the name plus the status from `useHpcStatus()`.
  - The body holds the "Show in sidebar" switch and that cluster's
    credentials.
  - A new `initialCluster?: Cluster` prop controls which section starts
    expanded. `NavRail` already owns the modal's open state and passes it
    from the popover's Settings link.
  - After a save that changes a cluster's credentials, or after Globus
    Connect completes, the modal calls `recheck(cluster)`.
- `ui/app/api/users/me/hpc-status/route.ts` is a proxy following
  `users/me/route.ts`.

## Risks / Trade-offs

- [Frontier `compute/resources` differs from Odo's] → The live run in task
  group 7 checks it once Frontier access works. Only that cluster's
  credential call would change.
- [Perlmutter's `compute/resources` differs and nobody can test it] → Any
  answer other than 200/401 is Couldn't verify, never a false green or red.
- [An expired Globus High Assurance session doesn't fail Transfer `ls` the way
  it fails the HTTPS surface] → The same live run tests it against Odo. If
  needed, add a `HEAD` on the collection home over HTTPS. It's one more call,
  with no change in approach.
- [Config drift between backend and MCP server] → Shared env var names, plus
  the defaults-parity test (Decision 2).
- [Load on facilities from many open tabs] → Per-user 60 s cache and the
  shared facility cache. The poll only runs while the tab is visible.
- [Upgrading resets OLCF connectivity] → Accepted: there are no deployments.
  The cards themselves tell researchers to paste per-cluster tokens.

## Migration Plan

This is one MR, committed one task group at a time. The new columns are
nullable and added automatically. `s3m_token` is left in place and unused.
There's no data migration: researchers paste per-cluster tokens after
upgrading. Rollback is reverting the MR. Anyone who pasted per-cluster tokens
would then need to paste one into the single field again.
