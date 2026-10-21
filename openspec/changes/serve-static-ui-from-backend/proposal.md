## Why

VISTA has two API surfaces: the FastAPI backend, which owns every endpoint, and
40 Next.js route handlers in `ui/app/api` that forward to it. The layer outlived
both of its reasons. The UI predates the backend, so its routes were kept as
proxies to leave the frontend unmodified. It was also a Backend-for-Frontend that
relayed a hosted load balancer's OIDC headers, but VISTA is now a single-user
desktop install and `auth.py` never reads them. What it costs today is a second
server process, a Node runtime bundled into every package only to run it, a
`ui` startup phase, and two path schemes for one API.

## What Changes

- **BREAKING** The backend's routers move under an `/api` prefix
  (`/api/projects/{name}/...`), and the UI calls those paths directly. The UI's
  `/api/chat`, `/api/files/{kind}?project_name=` shapes go away.
- The work the proxy routes did moves into the backend:
  - MCP's `CallToolResult` → `ExecutionResult` normalisation (`mcp/call`)
  - the tool-list shaping (`mcp/tools`)
  - the request validation and reshaping in the files, skills, skill-import
    and skill-from-output routes
- `ui/app/api/**` is deleted, including the `x-amzn-oidc-*` forwarding.
  `auth.py` is unchanged.
- The UI builds as a static export (`output: 'export'` → `ui/out`) instead of
  `output: 'standalone'`. FastAPI serves it on the same origin as `/api`, from a
  folder named by a backend setting. When that setting is unset or the folder is
  missing, the backend serves `/api` alone and logs why.
- Dev keeps `next dev` on `:3000` with hot reload. `/api/*` is rewritten to the
  backend there, and only there.
- **BREAKING** Default ports move off the commonly taken 8000/8001 to fixed,
  IANA-unassigned ones: backend (and so the app's origin) **38417**, vista MCP
  **38418**. Dev and packages use the same defaults, `VISTA_BACKEND_PORT` /
  `VISTA_MCP_PORT` still override them, and a port in use still fails startup
  with a named error. `VISTA_UI_PORT` is removed.
- **BREAKING** The package startup protocol drops its `ui` phase. The backend's
  `ready` event carries the URL the window opens.
- Packages ship `ui/out` instead of `.next/standalone`, and no longer bundle a
  Node runtime.
- Agent-emitted file links become `/api/projects/{name}/{outputs|uploads}/{path}`.
  **BREAKING**: `/api/files/...` links already saved in chat history stop
  resolving, and nothing rewrites or redirects them.
- The hermetic and screenshot Playwright suites serve `ui/out` with a plain
  static server instead of `next start`, keeping their in-page `/api` stub.
- The screencast toolkit serves a private `ui/out` from its own capture and
  replay server. Its recordings are keyed by `/api` paths. **BREAKING**:
  captures made before the change no longer replay, and the committed selftest
  fixture is rewritten.

## Capabilities

### New Capabilities

- `single-origin-serving`: FastAPI serves both the static UI and the `/api`
  surface on one origin. It covers how the UI folder is located and the
  API-only fallback, how unknown paths are answered, the dev rewrite, and the
  default ports.

### Modified Capabilities

- `screencast`: capture serves its own prebuilt static UI and answers `/api`
  on that one origin instead of running a private Next server. Recordings are
  keyed by the `/api`-prefixed path, so captures made earlier no longer replay.
- `validation-lane`: the hermetic browser suite serves the static UI from a
  plain file server instead of starting the Next server.
- `laptop-distribution`: build verification follows the startup events to
  the backend's readiness (there's no UI phase), checks that one origin serves
  both the UI and `/api`, and checks that no runtime ships for the UI.

## Impact

- **Backend:** router prefixes in `api/api.py`; new static mount and UI-folder
  setting in `config.py`; the default `port`; the default `mcp_url`; ported
  normalisation and validation logic in the MCP, files and skills routers; and
  `uri_map` in `agents/agents.py`.
- **UI:**
  - `ui/app/api/**` deleted; ~48 fetch call sites in `app`, `components` and
    `lib` re-pointed
  - `next.config.mjs`: export, dev-only rewrites, `serverExternalPackages`
    entry dropped
  - `lib/file-links.ts` and the report-link handling
  - `tests/chat-run-routes.test.ts` removed, with its coverage moved to pytest
  - the hermetic stub and Playwright configs
- **MCP server:** default `--port`.
- **Launch and packaging:**
  - `scripts/launch.sh`, `scripts/mac_dev_launcher.sh`, `scripts/build.sh`
  - `scripts/build_local_package.sh` (`bundle_node`, `stage_ui`, manifest)
  - `scripts/package_launcher.sh` and `.ps1`
  - `scripts/smoke_test_package.sh`, `scripts/nightly-validation.sh`
- **Electron:** `src/startup-protocol.js` (no `ui` phase) and the comments that
  cite port 3000.
- **Screencast toolkit:** `.claude/skills/vista-screencast/lib/{ui,server,session,paths}.mjs`,
  its tests, and `selftest/capture/responses.jsonl`.
- **Docs:** `AGENTS.md` and `docs/` wherever they name ports, the UI server or
  the `/api` proxy.
- **Open changes:**
  - `skill-hpc-jobs` (all tasks done) is archived before this change is
    applied. Its `hpc_jobs` → `hpcJobs` mapping in the skills proxy moves to the
    backend here.
  - `settings-modal-revamp`'s `/users/me/inference` proxy route is removed
    here. Its open task targets the backend path.
