# AGENTS.md

This file provides guidance to AI Agents when working with code in this repository.

## Project Overview

VISTA (Visual Intelligence for Scientific & Tooling Assistant) is a scientific assistant for molten salt thermophysical properties. It combines a Python FastMCP server (tools for sandboxed execution, RAG search, HPC job submission) with a Next.js frontend that orchestrates LLM-driven tool calls.

## Common Commands

### Full Development Setup
Starts both backend and frontend in tmux
```bash
./launch.sh tmux
```

### Backend (MCP Server)
Launches MCP server on :8000/mcp
```bash
cd mcp-server
uv run --env-file ../.env vista-mcp-server --transport=http
```

### Frontend (UI)
Launches Next.js dev server on :3000
```bash
cd ui
npm install
npm run dev
```

### MCP Apps (Widget UIs)
```bash
cd mcp-server/mcp-apps
npm install && npm run build
```

### Testing & Linting
```bash
cd mcp-server && pytest                      # Backend tests
cd ui && npm run lint                        # Frontend ESLint
curl http://localhost:3000/api/mcp/health    # Smoke test
```

## Architecture

### Request Flow
```
Browser → Next.js API routes (/api/*) → FastMCP server (:8000/mcp) → Tool execution
```
The browser never calls MCP directly. All calls go through `ui/app/api/*` route handlers.

### Backend: Modular MCP Tool Composition
The main server (`mcp-server/src/vista_mcp_server/server.py`) mounts independent sub-servers:
- **submit_job_mcp** — `ssh_login`, `submit_hpc_job`, `get_hpc_job_status`, `list_hpc_jobs` (HPC job submission to Frontier via SSH with elicitation-based credential prompts)
- **display_file_mcp** — Image/file rendering as base64 HTML
- **sandbox_mcp** — `run_bash`, `create_file`, `view` (Docker sandboxed execution)
- **rag_mcp** — Semantic search over research papers (ChromaDB + sentence-transformers)
- **OpenMetadata proxy** — Upstream MCP proxy for data catalog

Configuration is via `pydantic-settings` with `VISTA_MCP_` env prefix, reading from `.env` files (`mcp-server/src/vista_mcp_server/config.py`).

### Frontend: LLM Agent Loop
- `ui/app/api/chat/route.ts` — Main agentic loop: discovers MCP tools, calls LLM (OpenAI/Azure), executes tool calls with elicitation support, streams SSE events to browser, loops until done
- `ui/app/page.tsx` — Single-page React UI
- `ui/lib/mcp.ts` — MCP client with JSON-RPC helpers and elicitation-aware tool calling
- `ui/lib/skills.ts` — Skill discovery from `skills/**/SKILL.md`

### MCP Elicitation (Interactive Credential Prompts)
Tools that need user input mid-execution (e.g., SSH credentials) use MCP elicitation:
1. MCP tool calls `ctx.elicit(message, response_type)` on the server
2. The SSE chat stream sends an `elicitation` event `{ type, id, message, schema }` to the browser
3. `ui/components/ElicitationModal.tsx` renders a dynamic form (via RJSF — React JSON Schema Form) with Submit/Decline/Cancel actions
4. User submits → POST to `ui/app/api/chat/elicitation/route.ts` → resolves the pending promise in `ui/lib/elicitation-bridge.ts` → MCP tool receives credentials and continues

Key files:
- `mcp-server/src/vista_mcp_server/submit_job.py` — `ssh_login` uses `ctx.elicit()` with `SSHLoginInfo` dataclass
- `ui/lib/elicitation-bridge.ts` — In-memory promise bridge between SSE stream and form submission (5-minute timeout)
- `ui/components/ElicitationModal.tsx` — Schema-driven form with auto-detection of password fields
- `ui/app/api/chat/elicitation/route.ts` — POST endpoint receiving form responses

### Key Data
- **Salt DB**: `skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json` — evaluated thermophysical properties for 100+ salts
- **RAG DB**: `rag_db/chroma.sqlite3` — ChromaDB vector store of indexed research papers (embedding model: `google/embedding-gemma-300m`)
- **Skills**: `skills/salt-analysis/` and `skills/salt-prediction/` — domain scripts mounted into Docker sandbox at `/mnt/skills`

## Environment Variables

Backend env vars use `VISTA_MCP_` prefix (e.g., `VISTA_MCP_ALLOWED_URIS`, `VISTA_MCP_URI_MAP`, `VISTA_MCP_OMD_API_KEY`). Key backend config fields include `hpc_host`, `local_hpc_jobs_dir`, `remote_hpc_jobs_dir`, `rag_db_path`, `rag_model`, `omd_url`, `omd_api_key`, `allowed_uris`, `uri_map`, and `mcp_apps_dir`. Frontend uses `ui/.env.local` for `MCP_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`, etc. See `ui/README.md` for full list.

## Key Patterns

- All MCP tools are `async def` functions decorated with `@mcp.tool()`
- Docker sandbox mounts `skills/` as read-only and `data/output/` as writable
- The RAG pipeline (`build_rag.py`) indexes PDFs with citation extraction
- Frontend uses Tailwind CSS v4 and Next.js 16 App Router (file-based routing under `ui/app/`)
- Tools requiring user input mid-execution use MCP elicitation (`ctx.elicit()`) rather than separate credential-management flows
- HPC tools (`submit_hpc_job`, `get_hpc_job_status`, `list_hpc_jobs`) automatically call `ssh_login` which elicits credentials if no active SSH session exists
