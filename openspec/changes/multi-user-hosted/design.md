## Context

See proposal.md for why. The backend today has no identity at all: `api/api.py:269-290`
mounts every router with no auth dependency, `ui/app/api/_backend.ts:13-22` forwards no
identity by design across ~69 proxy route files, and one `local_settings` row
(`db/schemas.py:789-800`, read at ~29 call sites in 26 files) holds both installation
settings and the researcher's credentials. `GET /settings` returns those credentials
decrypted (`api/settings.py:16-19`), and `config.py:491-499` falls back to a constant key when
`VISTA_ENV` is `dev`, which Holly's pilot leaves it at.

The deployment flag already exists (`VISTA_BACKEND_DEPLOYMENT`, `config.py:313`) but only
changes where secrets live (`utils/secret_store.py:182`). The collaboration server already
verifies Obsidian tokens (`collaboration_server/.../services/identity.py:52-119`): it reads
`Authorization: Bearer`, checks signature via JWKS, issuer, `typ=Bearer`, `azp ∈ {apisix}` and
the client role `apisix/vista`, and skips `aud`.

Constraints: schema changes stay additive (`db/schema_check.py`), desktop installs must not
see a reset prompt, and SQLite in one uvicorn worker stays (decided).

## Goals / Non-Goals

**Goals:** one code path for both modes; isolation enforced below the routes so a new route
cannot forget it; the hosted limits enforced by the server; desktop behaviour unchanged.

**Non-Goals:** Postgres, more than one worker, moving in-memory registries into the DB,
in-VISTA sharing, an admin UI, Frontier on hosted (see proposal).

## Decisions

### D1. One `CurrentUser` dependency (Sam's carve-out)

`services/current_user.py` exports a FastAPI dependency `CurrentUser` that returns a
`UserTable` row. Desktop: the row with id `local`, created by `init_db`. Hosted: verify the
bearer token (D2), then get-or-create by `sub`, refreshing name and email. It is attached
once, as a router-level dependency in `api/api.py`, so no route can be mounted without it;
health and version routes are mounted outside it. The resolved user is also put in a
`contextvars.ContextVar` for code below the routes that has no request (D4, D11).

`UserTable` (`app_user`): `id` (str; `local` or a uuid), `sub` (unique, nullable), `display_name`,
`email`, `created_at`, `last_seen_at`. *Alternative:* revert `301fba76`'s `services/auth.py` and
`UserTable`. Rejected (O6): they carried dev headers, `ProjectMemberTable` and admin CRUD we
don't want, and the code moved a lot since; we restore only its tests.

### D2. Token path: gateway → Next.js → backend, verified at the backend

APISIX's browser-mode `openid-connect` keeps a session cookie and passes the access token
upstream (in `X-Access-Token` by default). Next.js's server-side proxy takes it from there (or
from `Authorization`) and sends `Authorization: Bearer` to the backend. The backend verifies it
with a copy of the collaboration server's `TokenVerifier` (~70 lines, `PyJWT` with
`PyJWKClient`, 5-minute key cache). Settings: `VISTA_BACKEND_OIDC_ISSUER`,
`…_OIDC_ALLOWED_AZP`, `…_OIDC_REQUIRED_ROLE`, defaults matching the collab server's.
*Alternatives:* trust a gateway header (rejected: a network mistake lets anyone pick an
identity); a shared package between the two servers (rejected for now: two separate uv
projects, and copying is less coupling than a third package for 70 lines).

The UI gets one catch-all proxy (`ui/app/api/[...path]/route.ts`) that forwards method, body,
query, streaming responses and the identity, replacing the per-route files that only relay.
Routes with real server-side logic stay and call the same forwarding helper.

### D3. Settings split: `user_settings` beside `local_settings`

A new `user_settings` table keyed by `user_id` holds the per-user fields listed in the
`user-accounts` spec; `local_settings` keeps the server fields. The old per-user columns stay
in `local_settings`, unread (additive schema); on a desktop start where `local` has no
`user_settings` row yet, the fields are copied once. A `SettingsView` (server + user, merged)
replaces `LocalSettingsDep` at the ~29 call sites, so most call sites change only their
import. Hosted server settings come from `config.Settings` (environment); `PUT /settings`
ignores server fields when hosted and says so. Responses expose `has_*` for every credential
(fix-first, both modes), extending today's `has_github_token` pattern.

### D4. Ownership: nullable `user_id`, filtered at the data layer

`user_id` (nullable FK to `app_user`) is added to project, chat_session, chat_run,
campaign_run, hpc_job, debate_run, background_task, slack_link, skill and knowledge_base.
`init_db` fills nulls with `local` on every start (cheap, idempotent). Child rows (messages,
files, posts) are owned through their parent and are always reached through it. Every query
for an owned table goes through helpers in `db/ownership.py` (`owned(select(T), user)`,
`get_owned(session, T, id, user)` returning `None` → `404`); `services/project.py:31`'s
name lookup becomes owner-scoped, which covers every name-keyed route. Global seeded and
bundled skills and the OLCF docs corpus have `user_id = NULL` *and* a `bundled`/`seeded`
marker, and are readable by all and writable by none.

Unique constraints: on SQLite `create_all` never alters an existing table, so desktop DBs
keep global `uq_skill_name`/`uq_knowledge_base_slug` (harmless with one user) while new
databases (hosted starts fresh) get `(user_id, name)` / `(user_id, slug)`. `schema_check`
does not compare constraints. *Alternative:* filter in each route (rejected: 69 places to
forget).

### D5. Background paths resolve the row's owner

Job watches (`services/background_tasks.py:1050`), the campaign monitor
(`campaign_monitor.py:213`), Slack turns, inbox wakes and startup recovery
(`api/api.py:84-135`) load the owner from the row they act on and set the current-user
context before touching settings or agents. A helper `as_user(user_id)` does it, and a test
fails any background entry point that reads settings without one.

### D6. Runtime is keyed by user

The agent pool key becomes `(user_id, chat_session_id, project_id)`
(`services/project_agent.py:17,69,118`); a settings change evicts only that user's entries.
`_build_vista_metadata` (`agents/agents.py:1002`) sends only the current user's HPC fields
plus the server's allowed clusters, not the whole settings dump. Globus pending logins are
keyed `(user_id, cluster)` (`services/globus_auth.py:171`). Sandbox volumes become
`volumes/{user_id}/{project_id}`; desktop keeps `volumes/{project_id}` for `local` so existing
sandboxes survive. The sandbox subprocess gets an allowlisted environment, not `**os.environ`
(`agents/agents.py:402-430`). Caps (4 per user when hosted, the total on a desktop; 40
total) live in the pool's eviction: at the user's cap their least-recently-used idle one, at
the server's anyone's (decided 2026-11-25: a newcomer is not locked out for the 30-minute TTL
by others' idle ones), and refuse when none is idle. One code path; only the default differs.

### D7. The hosted flag in the UI

The version route adds `deployment`. A `useDeployment()` hook (fetched once, cached) drives
every hidden control; nothing reads a `NEXT_PUBLIC_` variable for it. Each refusal is in the
backend or MCP server first, then hidden in the UI: updates (`api/updates.py:93-160`),
Electron-only actions (`electron/src/main.js:629,781-877`, already absent in a browser, but the
UI hides their buttons), linked folders, NERSC, Custom, `/mcp/call` (`api/mcp.py:41`), skill
import hosts (`agents/skill_repo.py:134`, resolve then reject private ranges).

### D8. Base path at build time

`next.config` reads `VISTA_BASE_PATH` (default empty) into `basePath`. Next.js prefixes
`<Link>` and assets but not `fetch('/api/…')` or `EventSource`, so client calls go through
one `apiUrl()` helper; an ESLint rule forbids string literals starting `/api/` in client code.

### D9. Odo-only

A server setting `allowed_clusters` (default `["odo"]` when hosted, all when desktop) flows
into the MCP metadata (D6). The MCP job and transfer tools refuse any cluster not in it;
`hpc_status` filters to it; the settings save refuses others. S3M save on hosted calls the
existing `odo_introspect_url` and accepts only `securityEnclave` open. The shared Globus pair
(`globus_token`, used for every cluster as a fallback in `require_globus_token`) is refused when
hosted, and the fallback is off. The agent's instructions get one hosted rule: only the
allowed clusters exist here.

### D10. Inference: a Server provider

A `server` preset in `agents/inference.py` reads endpoint, model and key from server
settings (Beam: `http://127.0.0.1:8100/v1`), offered and default only when hosted. MAG and
OLCF keys are user settings. `custom` is refused when hosted. Beam's availability is a cached
probe of its `/v1/models`, shown in the picker.

### D11. Collaboration sign-in from the request

When hosted, `services/collaboration.py`'s client takes its token from the current request's
access token (held in the request context, D1) instead of `collaboration_token`. Outside a
request there is no token, and collaboration reports "needs the web app". Keycloak access
tokens are short-lived (minutes), so the token is never stored.

### D12. Slack

`slack_link.user_id` (nullable, backfilled to `local`). Redeeming a link code sets it to the
current user. The Slack service resolves the link's user for every event and runs the turn as
that user (D5). Hosted Slack tokens come from the server's secrets directory, as the existing
spec already allows.

### D13. MCP server on loopback with a shared secret

Both servers bind `127.0.0.1` in both modes. The backend sends `X-Vista-MCP-Secret` (a random
value generated at startup and passed to the MCP server's environment by `launch.sh`, or set in
the unit's secrets file when hosted); the MCP server's HTTP app refuses requests without it.

### D14. Dev issuer for local multi-user

`scripts/dev-gateway.py` (a `uv run --script` with PyJWT and cryptography): generates an RSA
key, serves `/realms/dev/protocol/openid-connect/certs`, a login page that sets a cookie for a
chosen name, and a reverse proxy in front of the UI that injects `X-Access-Token` with a
minted token (`azp=apisix`, the role, `typ=Bearer`), just as APISIX does. `./launch.sh --hosted`
starts it on `:3100`, sets `VISTA_BACKEND_DEPLOYMENT=hosted`, the issuer, a generated
encryption key and `VISTA_ENV=dev` (which is what lets a loopback issuer through). Tests mint
tokens with an in-process key fixture instead.

## Delivery plan

Groups in tasks.md run through `apply-in-groups-delegate`. Each group is a node on the
implementation dashboard; independent groups run in parallel in their own worktrees branched
from `vfs-multi-user`, and each stops at a gate: tests re-run by the parent, a merge check
against the current integration branch and any group finishing alongside it, a code review,
then Sam's approval before a local merge. Nothing is pushed without Sam's word.

```
wave 1 (parallel)  G1 fix-first   G2 hosted flag + base path   G3 dev gateway + UI proxy
                   G4 CurrentUser: Sam's carve-out (scaffolded, then Sam writes it)
                                  │
gate               ◆ G4 done and reviewed (wave 2 needs the user model)
                                  │
wave 2 (parallel)  G5 settings split        G6 ownership + data-layer filtering
                                  │
wave 3 (parallel)  G7 authorization + background owners   G8 runtime per user + sandbox caps
                   G9 Odo-only (needs G2, G5)             G10 inference server provider (G2, G5)
                                  │
wave 4 (parallel)  G11 collaboration auto sign-in (G3, G5)   G12 Slack (G6, G7)
                                  │
wave 5             G13 isolation suite + two-browser check   G14 docs + desktop regression
                                  ▼
gate               ◆ multi-user-hosted complete → holly-deployment
```

## Risks / Trade-offs

- [A missed owner filter leaks data] → filtering lives in `db/ownership.py` helpers, and the
  isolation suite enumerates routes and fails on one without a case.
- [Background code reads settings without a user] → `SettingsView` raises outside a user
  context; a test drives each background entry point.
- [The catch-all proxy breaks a streaming route] → G3 keeps the existing SSE relay tests and
  adds one through the catch-all.
- [Access tokens expire mid-stream] → verified at stream start only; APISIX renews the session,
  and later requests carry the new token.
- [Desktop regression] → G14 runs the packaged smoke test and a desktop-DB upgrade test with a
  pre-change `vista.db` fixture.
- [Parallel groups conflict in `schemas.py` and `api.py`] → wave 2 groups touch disjoint parts;
  the parent's merge check runs before each gate.
- [SQLite write contention] → acceptable for a handful of researchers; WAL already on.

## Migration Plan

Desktop: additive columns and tables, backfilled to `local` on start; no reset. Hosted: a fresh
data folder (pilot data is not migrated). Rollback: the old version ignores the new columns
and tables; per-user settings saved only after the upgrade would be lost on a downgrade.

## Open Questions

- Whether the catch-all proxy can replace all relay-only routes in G3 or only the ones G6/G7
  touch; it doesn't change behaviour either way.
