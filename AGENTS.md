# AGENTS.md

This file provides guidance to AI Agents when working with code in this repository.

## Project Overview

VISTA (Visual Intelligence for Scientific & Tooling Assistant) is a scientific assistant for molten salt thermophysical properties. It provides an "Agent as a Service" API, and a Agentic chat-bot UI.

## OpenSpec

Spec-driven changes live under [`openspec/`](openspec/). Main specs are in
`openspec/specs/`; active proposals are in `openspec/changes/`.

In agent chat (not the terminal):

- `/opsx:explore` — think through a change before committing to artifacts
- `/opsx:propose` — draft proposal, delta specs, design, and tasks
- `/opsx:apply` — implement an open change’s `tasks.md`
- `/opsx:archive` — merge delta specs into main specs after the change ships

These commands and their skills are committed under `.claude/` (Claude Code is the
default). For Cursor, install the opsx commands **globally** so they work in every
project (not just this repo) — Cursor uses flat command names like `/opsx-apply`:

```bash
tmp=$(mktemp -d) && (cd "$tmp" && openspec init --tools cursor --force) \
  && mkdir -p ~/.cursor/commands ~/.cursor/skills \
  && cp "$tmp"/.cursor/commands/opsx-*.md ~/.cursor/commands/ \
  && cp -R "$tmp"/.cursor/skills/openspec-* ~/.cursor/skills/ && rm -rf "$tmp"
```

(Cursor already reads skills from `.claude/skills/` inside this repo; the global
copy just makes them available everywhere too.)

CLI helpers: `openspec list`, `openspec list --specs`, `openspec validate --all`,
`openspec show <name>`. Project context is in `openspec/config.yaml`.

Testing roadmap requirements are OpenSpec-first — canonical specs are in
[`openspec/specs/`](openspec/specs/) and open changes under
[`openspec/changes/`](openspec/changes/).

## Common Commands

### Full Development Setup
Starts the vista_mcp_server, backend, and frontend, logging to logs/mcp.log, logs/backend.log, and logs/ui.log respectively, and opens the UI in the VISTA window.
```bash
./launch.sh logs                # services + the VISTA window
./launch.sh logs --no-electron  # services only; use a browser (e.g. over SSH)
```

Note that the `./launch.sh` script will not terminate until the window is closed or the script is cancelled, and then will automatically clean up all 3 processes. With no display it fails and asks for `--no-electron` rather than falling back to a browser.

On Windows, run `launch.sh` and `build.sh` from Git Bash (installed with Git for Windows).

To build everything without launching, run
```bash
./scripts/build.sh
```

Release packages: `scripts/build_local_package.sh` builds and smoke-tests one for the host it
runs on. Tagged releases are built by `.github/workflows/release.yml` on the GitHub mirror
(`Genesis-VISTA/vista`), whose job steps live in `.github/scripts/` so they run the same
locally. See [`docs/releasing.md`](docs/releasing.md). The mirror overwrites GitHub-only
commits, so workflow changes land on GitLab like everything else.

### VISTA window (Electron)
`electron/` is a window onto the UI and nothing else: it loads the `--url` it is given and never
starts services. The launchers own its lifetime, and closing it stops VISTA.
```bash
./launch.sh logs              # dev stack in the window (installs it via build.sh --electron)
cd electron && npm test       # routing rules (hermetic, in PR CI)
cd electron && npm run test:e2e  # window behaviour via Playwright; needs a display
```
Where a link goes is decided by origin alone in `electron/src/routing.js`: VISTA's own origin
stays in the app, other http(s) goes to the system browser, and everything else is refused. So
UI links need no Electron-specific code. The prebuilt macOS package ships it as
`app/window/VISTA.app`, the Linux package as `app/window/VISTA`, and the Windows package as
`app/window/VISTA.exe`, all found through the manifest's `window.exe`. There is no browser mode: the launcher refuses a session that cannot show
the window. `VISTA_NO_WINDOW=1` starts the services alone, for the build's smoke test only.

VISTA's icon is `electron/assets/icon.svg`. Every other icon file is made from it by
`cd electron && npm run icons` (`scripts/make-icons.js`, which renders with Electron itself):
`icon.icns`, `icon.ico` and `icon.png` beside it, and the UI's `ui/app/favicon.ico` and
`ui/app/icon.svg`. They are committed, so a build never regenerates them; rerun it after
editing the SVG.

On Linux, whether the window gets `--no-sandbox` is decided in one place,
`electron/linux/window-sandbox`. The package launcher, `./launch.sh` and the
build's smoke test all call it, so don't hardcode the flag anywhere else. It prints the flag
only when the host blocks Chromium's sandbox: as root, or where unprivileged user namespaces
are blocked and VISTA's AppArmor profile (`electron/linux/vista-window.apparmor`, shipped next
to the window) isn't installed, as on stock Ubuntu 24.04. Each time, it says why on stderr.
While the window is unsandboxed, `main.js` sends PDFs to the system browser. Test with
`cd electron && npm test`, which runs `window-sandbox.test.js` hermetically, and the Linux e2e
container command in `docs/validation-lane.md`.

### MCP Server (vista_mcp_server)
Launches vista_mcp_server on :8000/mcp (HPC, RAG, display_file tools)
```bash
cd mcp_servers/vista_mcp_server
uv run vista-mcp-server --transport=http
```

The dev_mcp_server (sandbox tools: run_bash, create_file, view) is launched automatically per-agent via STDIO by the backend — it does not need to be started manually.

### Backend
Launch the PydanticAI backend

```bash
cd backend
uv run vista-backend
```

### Frontend (UI)
Launches Next.js dev server on :3000
```bash
cd ui
npm install
npm run dev
```

### Testing & Linting

Testing roadmap (milestones A–D, VISTAGuard out of scope): canonical specs in
`openspec/specs/` and open changes `openspec/changes/milestone-*`.

Hermetic PR CI uses `-m "not live and not hpc and not sandbox"`. Live / HPC /
Playwright validation is schedule-or-manual only — see
[`docs/validation-lane.md`](docs/validation-lane.md) and
`./scripts/nightly-validation.sh`.

Mirror GitLab CI locally (targets: `backend`, `ui`, `mcp`, `electron`; actions: `lint`, `test`):
```bash
./scripts/ci-local.sh                  # all lint + test
./scripts/ci-local.sh lint             # lint only
./scripts/ci-local.sh backend test     # backend pytest only
./scripts/ci-local.sh ui mcp lint      # UI + MCP lint
./scripts/ci-local.sh electron         # window typecheck + routing tests
./scripts/ci-local.sh install-hooks    # git pre-commit runs lint --fast
```

Or run pieces directly:
```bash
cd mcp_servers/vista_mcp_server && uv run --extra dev pytest  # HPC/RAG MCP tests
cd mcp_servers/dev_mcp_server && uv run pytest  # Sandbox/view tests
cd ui && npm run lint                           # Frontend ESLint
cd backend && uv run --extra dev pytest -vv     # Backend tests
curl http://localhost:3000/api/mcp/health       # Smoke test
```

Use Playwright (install globally if not present) to interact with the browser, take screenshots, and manually test the frontend.

The vista MCP server boots without any interactive login — HPC job submission authenticates
with per-user tokens (S3M / NERSC IRI for compute, Globus for file ops) supplied via the UI, each
connected once per cluster in the settings modal rather than exported anywhere. Set
`VISTA_MCP_DISABLE_SERVERS=submit_job` if you want to skip mounting the job tools entirely.

Odo and Frontier file operations go through Globus, split across two of its surfaces
([`lib/globus.py`](mcp_servers/vista_mcp_server/src/vista_mcp_server/lib/globus.py)). **File
contents** move over the **HTTPS interface** — ordinary `GET`/`PUT` against the cluster's own
collection, authorized by a bearer token. **Directory listings and `mkdir`** stay on the
**Transfer API**, which the HTTPS interface has no answer for. An HTTPS request needs no
collection on VISTA's side, so VISTA runs no Globus endpoint of its own: nothing to install,
nothing to start, and no second collection that could have been created by the wrong identity.

Two consequences worth knowing before touching that module, both established by probing the live
collections. `HEAD` is the only way to learn a file's size — a plain `GET` returns no
`Content-Length` and a ranged `206` reports its total as `*`. And only explicit `start-end`
ranges work; a suffix range (`bytes=-N`) answers `416`, because the server streams from the
filesystem without seeking to the end. Job logs are therefore tailed incrementally: `HEAD` for
the size, one range for what is new since the last poll.

Both OLCF collections are High Assurance with a 3-day authentication timeout that refreshing a
token does **not** reset, and a Frontier queue wait routinely exceeds it. So an expired session
is an expected outcome, not an exceptional one, and it must never be reported as an empty output
directory — that confusion is the bug this transport exists to remove. `GlobusSessionExpired`
(401, from either surface) and `GlobusFileNotFound` (404) are separate types for exactly that
reason; branch on them rather than on a message.

The credential is per-cluster and per-user, and is a *pair* of refresh tokens — Globus issues one
per resource server, and the collection is its own. A researcher's own Odo or Frontier pair wins,
falling back to one pair they connected for both. There is no deployment-wide Globus login: every
file operation acts as the researcher's own identity, so the facility decides what they may read
and write. Each source counts only when it has both halves: one alone lists a directory it
cannot read.
Absent Globus is never fatal; it costs only Odo and Frontier's file operations, nothing else.

## Hypothesis Lab forum

The Hypothesis Lab's debates live in a plain git repository per project (the
project's `forum_repo_url`); the forge is the only server. The client is
[`services/forum_git.py`](backend/src/vista_backend/services/forum_git.py), the format
is [`docs/forum-git-format.md`](docs/forum-git-format.md), and hosting is
[`docs/hypothesis-forum-hosting.md`](docs/hypothesis-forum-hosting.md).

- **It needs system git ≥ 2.34** and `VISTA_BACKEND_FORUM__ENABLED=true`. Without
  git the lab is off with the reason on the page; nothing else in VISTA depends on
  it. `services/git_check.py` does the check without ever running macOS's
  `/usr/bin/git` install shim.
- **Posting is local-first.** A post is a commit in
  `data/forum-git/<project-id>/repo.git`, published by fetch–replay–push, never
  force-pushed. Which posts are *ours* lives in that directory's `outbox.db`, not in
  `vista.db`: the forum is written from code inside open app-DB transactions, and
  an outbox there deadlocked on them. Keep it out.
- **Tests.** `test_forum_git.py` runs the real client against temporary bare
  repositories as two installs; orchestrator/API/simulation tests use
  `tests/harness/fake_forum.py` (the `fake_forum` / `client` fixtures).

## Text encoding

Always pass an explicit `encoding="utf-8"` to anything that reads or writes text in
Python: `open()`, `Path.read_text()` / `write_text()`, `os.fdopen(..., "w")`, and
`subprocess` calls with `text=True`. The default encoding is the locale's, which is not
UTF-8 on Windows, so leaving it out breaks there. The backend and both MCP servers set
`error::EncodingWarning` in their pytest config, so any call that omits it fails CI.

## NextJS
ALWAYS read docs before coding

Before any Next.js work, find and read the relevant doc in `ui/node_modules/next/dist/docs/`. Your training data is outdated — the docs are the source of truth.
