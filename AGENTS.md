# AGENTS.md

This file provides guidance to AI Agents when working with code in this repository.

## Project Overview

VISTA (Visual Intelligence for Scientific & Tooling Assistant) is a scientific assistant for molten salt thermophysical properties. It provides an "Agent as a Service" API, and a Agentic chat-bot UI.

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
```bash
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

## NextJS
ALWAYS read docs before coding

Before any Next.js work, find and read the relevant doc in `ui/node_modules/next/dist/docs/`. Your training data is outdated — the docs are the source of truth.
