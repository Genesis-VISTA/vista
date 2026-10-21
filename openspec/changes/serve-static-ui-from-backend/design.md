## Context

See proposal.md (Why). The facts the approach rests on:

- **Static export is possible.** Every UI page is a client component, and no
  page route has a dynamic segment (`/`, `/projects`, `/skills`, `/skill-hub`,
  `/datasets`, `/hypothesis-lab`, `/knowledge-bases`,
  `/knowledge-bases/project`). Outside `ui/app/api` there is no middleware,
  server action, `next/headers`, `next/image` or server-only import. So
  `output: 'export'` is blocked only by the route handlers this change deletes.
- **The backend has no prefix and serves nothing static.** Its routers mount at
  `/projects`, `/skills`, `/knowledge-bases` and `/users` with no prefix. Three
  of those collide with page routes. It mounts no static files. `FastAPI(dependencies=[Depends(get_user)])`
  applies to routes only, not to mounted sub-apps.
- **The proxies are not 1:1.** About 23 of the 40 route handlers turn a
  `?project_name=` query or a body field into a `/projects/{name}/...` path. A
  few transform data:
  - `mcp/call`: `CallToolResult` → `ExecutionResult`
  - `mcp/tools`
  - `files/*`: path-segment validation
  - `skills/*`: e.g. `hpc_jobs` → `hpcJobs`, from `skill-hpc-jobs`
  - `skills/import`, `projects/[name]/skills/from-output`
- **Nothing else needs Node.** The package bundles Node only for
  `.next/standalone/server.js`.
- **The screencast toolkit runs a private UI.** It runs a private standalone
  build (`lib/ui.mjs`) pointed at its own capture or replay server
  (`lib/server.mjs`), which plays the backend.
- **Dev rewrites still work.** `next dev` honours `rewrites` when
  `output: 'export'` is set; Next only warns. Proxying SSE through it is
  unverified.

## Goals / Non-Goals

**Goals:**
- The UI's components keep their data shapes. Only the URLs they fetch
  change, so the hermetic fixtures change keys, not bodies.
- In packages, one server process serves the UI. In dev, a hot-reloading
  dev server serves it.

**Non-Goals:**
- Changing response shapes beyond what moves out of the proxies, or tidying
  the API while it moves.
- Removing hosted-mode code from the backend (`auth.py`, PALISADE).
- Moving origin-keyed `localStorage` state into the backend.

## Decisions

### D1. `/api` prefix at `include_router`

Every `app.include_router(...)` in `api/api.py` gains `prefix="/api"`,
including PALISADE's. The routers' own paths don't change. FastAPI's
`openapi_url`, `docs_url` and `redoc_url` move under `/api` too
(`/api/openapi.json`), so the whole non-UI space sits under one prefix.
`scripts/mac_dev_launcher.sh` waits on `/openapi.json` and follows.

The alternative, a parent `APIRouter(prefix="/api")` holding every router,
would also work. It's one more indirection for no gain.

### D2. Unknown `/api` paths answer JSON before the UI mount

A catch-all `/api/{path:path}` route for every method raises a 404, which
FastAPI renders as JSON. It's registered after the real routers and before
the static mount. Without it, an unknown API path would fall through to the
UI and come back as an HTML 404.

### D3. A small static handler, not Next's `trailingSlash`

The export writes `skills.html` for `/skills`. Starlette's `StaticFiles`
with `html=True` resolves only `dir/index.html` and a root `404.html`. So
the mount is a `StaticFiles` subclass with this lookup:
- the path as given, then `{path}.html`, then `{path}/index.html`
- otherwise `404.html` with status 404

It is mounted at `/` after every route.

Setting `trailingSlash: true` so the stock handler works was rejected. In
`next dev` it also redirects `/api/foo` to `/api/foo/`, and FastAPI
redirects back, which loops.

### D4. UI folder setting and the API-only fallback

The setting is `Settings.ui_dir: Path | None` (`VISTA_BACKEND_UI_DIR`). At
import time the backend checks that the folder holds `index.html`. If it
does, the mount is added. If not, it logs one line naming the reason and
serves `/api` only.
- `./launch.sh` leaves the setting unset, because `next dev` serves the
  pages.
- The package launchers set it to the shipped UI folder.
- `uv run vista-backend` with no setting is the API-only server that tests
  and scripts already assume.

### D5. Dev: rewrites only when not exporting

`next.config.mjs` sets `output: 'export'` only for `next build`. Rewrites are
defined only in development: `/api/:path*` → `${VISTA_BACKEND_URL}/api/:path*`,
where `VISTA_BACKEND_URL` comes from the `.env` the config already loads,
defaulting to `http://127.0.0.1:38417`. Keying the switch on the phase
(`PHASE_DEVELOPMENT_SERVER` passed to the config function), not
`NODE_ENV`, keeps a production-mode `next dev` correct.

Task group 1 verifies SSE through this proxy. The proposal leaves the
fallback open on purpose: if events arrive only when a stream ends, the
change stops there for a decision.

Why not the alternatives:
- Dev-only CORS against the backend's port would make dev cross-origin while
  packages are not, so the window's origin-based link routing would differ.
- FastAPI proxying to `next dev` would put proxy code in the backend.

### D6. Moved logic keeps the old response shapes

Each transforming route's logic moves into the backend endpoint it called:
- `mcp/call` returns `ExecutionResult`, ported from the route's
  `createEnvelope` / HTML and image extraction / `display_file` detection.
- `mcp/tools` returns what the proxy returned.
- `files` validates segments with the same rules (no empty, `.` or `..`),
  enforced server-side.
- `skills` emits `hpcJobs`.
- `skills/import` and `from-output` take the request shape the UI already
  sends.

Pydantic response models pin each shape, and pytest covers the ported
transforms. Where the UI sends `project_name` in a body or query, the UI
call site moves it into the path. The backend does not grow
query-parameter aliases.

### D7. One UI helper for API paths

`ui/lib/api.ts` exports `api(path)`, which returns `/api${path}`, plus
`projectApi(name, path)`, which encodes the project name into
`/api/projects/{name}${path}`. Every call site uses one of them, so the
prefix lives in one place and the hermetic stub's `/api/**` keying still
holds. Literal strings at about 48 sites were the alternative. They are
grep-able but make the prefix a convention rather than a fact.

### D8. Startup protocol v2: no `ui` phase

`electron/src/startup-protocol.js` drops the `ui` phase. `url` becomes
required on the `backend` phase's `ready` event instead, carrying
`http://127.0.0.1:${BACKEND_PORT}/`. The launchers emit
`"protocol": 2` and Electron accepts only 2. Electron and the launchers
always ship in one package, so no compatibility path exists. The version
still bumps so a mismatched dev pairing fails as an application-version
error, as the `desktop-window` spec requires, instead of hanging waiting
for a `ui` event. Any failure code that only the `ui` phase emitted is
removed from both launchers and the message table together. The unit test
that cross-checks them keeps them consistent.

### D9. Ports

| Port | Before | After | Where it's set |
|---|---|---|---|
| Backend (and the app's origin) | 8001 | 38417 | `Settings.port`, both package launchers, `launch.sh`, `mac_dev_launcher.sh`, `nightly-validation.sh`, the screencast `session.mjs` default |
| vista MCP | 8000 | 38418 | `server.py --port`, `Settings.mcp_url`, the same launchers |
| UI (`VISTA_UI_PORT`) | 3000 | removed | Packages no longer have it; dev keeps `next dev` on `:3000` |

Both new ports were checked against IANA's registry (`/etc/services`): the
whole 38400–38499 range is unassigned. The port preflight keeps its
failure codes, minus the UI port. The smoke test keeps its own override
ports.

### D10. Packaging

- `build_local_package.sh`: `stage_ui` copies `ui/out` into the package's UI
  folder. `bundle_node`, the nodejs.org download, the Node version and
  platform table, and the manifest's Node entries are deleted.
- `package_launcher.sh` and `.ps1`: they drop `node/bin` on PATH, the `ui`
  phase and `VISTA_UI_PORT`, and export `VISTA_BACKEND_UI_DIR` instead.
- `smoke_test_package.sh`: checks `/` returns the index page and
  `/api/projects` returns JSON, both on the backend port.
- `build.sh`'s prod UI build becomes the export build.

### D11. Hermetic and screenshot suites: a shared static server

`ui/scripts/serve-static.mjs` serves `ui/out` with the same lookup as D3,
using only `node:http`, so there's no new dependency. Both Playwright
configs run `next build && node scripts/serve-static.mjs`. The in-page stub
(`e2e-hermetic/stub.ts`) keeps its mechanism and updates its route keys to
the backend paths. `tests/chat-run-routes.test.ts`, which tests deleted
handlers, is removed. Any behaviour of those handlers that moved into the
backend gets pytest coverage under D6; passing errors through is native
now and needs none.

### D12. Screencast: one server serves the private UI and answers `/api`

`lib/ui.mjs` builds the private copy with the export output and returns its
`out/` folder. `lib/server.mjs` serves that folder (D3 lookup) for non-`/api`
paths on its own port:
- replay answers `/api` from the capture
- capture forwards `/api` paths unchanged to the live backend, which now
  serves them at the same paths

`PORTS.ui` is removed, so a session is one server. Keys are the path as
requested, `/api` included. `selftest/capture/responses.jsonl` is rewritten
to the prefixed paths. Captures under `~/.vista/screencasts` made earlier
stop replaying, which the spec makes explicit.

### D13. Agent file links

In `agents/agents.py`, `uri_map` becomes
`/api/projects/{name}/{outputs|uploads}/{path}`, with the project name
URL-encoded. `ui/lib/file-links.ts` and the report-link handling recognise
the new form only.

## Risks / Trade-offs

- **SSE could be buffered through the dev proxy**, so chat would look stuck
  in dev. → It's verified first (task 1.3); if it fails, work stops for a
  decision, as the proposal leaves the fallback open.
- **Static export might reject a hook used without a boundary.** For example,
  `useSearchParams` outside `<Suspense>` passes `next dev` but fails
  `next build` under export. → The export build runs in task group 1, before
  anything depends on it. Fixes are local wrappers.
- **Client navigation loads RSC payload files.** Pages fetch `.txt` files next
  to their HTML, and the D3 handler has to serve them as files, not fall back
  to `404.html`. → D3 tries the path as given first, and tasks 3.x check
  navigation between every page.
- **`localStorage` resets once.** The origin moves from `:3000` to
  `:38417`, so on first launch after upgrading the active project, active
  chat, theme and rail state reset. → Accepted, no migration.
- **Saved file links and old captures break.** `/api/files/...` links in chat
  history and pre-change screencast captures stop resolving. → Accepted
  (proposal, specs).
- **Static assets skip the login dependency.** They hold no user data, and
  every `/api` route keeps `get_user`. → Spec'd (single-origin-serving).

## Migration Plan

- **Before applying:** archive `skill-hpc-jobs`, so its spec merges and its
  proxy mapping is moved here deliberately rather than lost.
- **Data:** none.
- **Researchers:** the next package is a normal upgrade; the one-time
  `localStorage` reset is the only visible effect.
- **Developers:** existing `.env` files that pin `VISTA_BACKEND_URL` or
  `VISTA_MCP_URL` to the old ports keep working only if both sides are
  pinned. The docs say to drop them.
- **Rollback:** revert the MR.
