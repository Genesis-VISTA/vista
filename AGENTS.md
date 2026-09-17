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
Starts the vista_mcp_server, backend, and frontend, logging to logs/mcp.log, logs/backend.log, and logs/ui.log respectively.
```bash
./launch.sh logs
```

Note that the `./launch.sh` script will not terminate until cancelled, and then on cancel will automatically clean up all 3 processes.

To build everything without launching, run
```bash
./scripts/build.sh
```

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

Mirror GitLab CI locally (targets: `backend`, `ui`, `mcp`; actions: `lint`, `test`):
```bash
./scripts/ci-local.sh                  # all lint + test
./scripts/ci-local.sh lint             # lint only
./scripts/ci-local.sh backend test     # backend pytest only
./scripts/ci-local.sh ui mcp lint      # UI + MCP lint
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
with per-user tokens (S3M / NERSC IRI) supplied via the UI at tool-call time, plus
deployment-wide Globus refresh tokens (`VISTA_MCP_ODO_GLOBUS_REFRESH_TOKEN` for Odo's open
enclave, `VISTA_MCP_FRONTIER_GLOBUS_REFRESH_TOKEN` for Frontier's moderate enclave) for file
ops. Set
`VISTA_MCP_DISABLE_SERVERS=submit_job` if you want to skip mounting the job tools entirely.

Odo and Frontier file operations are brokered by Globus Transfer between two collections, so
this machine has to be one — which is what Globus Connect Personal makes it. Globus ships a
scriptable build for Linux only, so on every other platform it runs in a microsandbox microVM:
no container runtime, no daemon, and a separate image from the one the agent executes generated
code in. [`lib/gcp_vm.py`](mcp_servers/vista_mcp_server/src/vista_mcp_server/lib/gcp_vm.py) owns
the whole flow; `scripts/launch_globus.py` is a thin wrapper over it for checkouts, and the
packaged launcher runs the same entry point as `python -m`. Setup and startup are gated on a
refresh token being configured and are never fatal — absent transfer costs Odo and Frontier,
and nothing else.

## NextJS
ALWAYS read docs before coding

Before any Next.js work, find and read the relevant doc in `ui/node_modules/next/dist/docs/`. Your training data is outdated — the docs are the source of truth.
