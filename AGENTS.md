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
- **submit_job_mcp** — `submit_hpc_job`, `get_hpc_job_status`, `get_hpc_job_outputs`, `list_hpc_jobs`, `cancel_hpc_job`. Dispatches by `cluster=` arg to either Odo (OLCF, via S3M API + SSH/SCP for file access) or Perlmutter (NERSC, via IRI API through the amscrot SDK — no SSH). Confirmation via MCP elicitation.
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

### MCP Elicitation (Interactive Confirmation Prompts)
Tools that need user confirmation mid-execution use MCP elicitation:
1. MCP tool calls `ctx.elicit(message, response_type)` on the server
2. The SSE chat stream sends an `elicitation` event `{ type, id, message, schema }` to the browser
3. `ui/components/ElicitationModal.tsx` renders a dynamic form (via RJSF — React JSON Schema Form) with Submit/Decline/Cancel actions
4. User submits → POST to `ui/app/api/chat/elicitation/route.ts` → resolves the pending promise in `ui/lib/elicitation-bridge.ts` → MCP tool receives the response and continues

Key files:
- `mcp-server/src/vista_mcp_server/submit_job_mcp.py` — `submit_hpc_job` uses `ctx.elicit()` with a `Confirmation` schema before submitting
- `ui/lib/elicitation-bridge.ts` — In-memory promise bridge between SSE stream and form submission (5-minute timeout)
- `ui/components/ElicitationModal.tsx` — Schema-driven form rendered for any elicitation request
- `ui/app/api/chat/elicitation/route.ts` — POST endpoint receiving form responses

### Key Data
- **Salt DB**: `skills/salt-analysis/assets/Molten_Salt_Thermophysical_Properties.json` — evaluated thermophysical properties for 100+ salts
- **RAG DB**: `rag_db/chroma.sqlite3` — ChromaDB vector store of indexed research papers (embedding model: `google/embedding-gemma-300m`)
- **Skills**: `skills/salt-analysis/` and `skills/salt-prediction/` — domain scripts mounted into Docker sandbox at `/mnt/skills`

## Environment Variables

Backend env vars use `VISTA_MCP_` prefix. Odo-side: `hpc_host`, `hpc_account`, `local_hpc_jobs_dir`, `remote_hpc_jobs_dir`, `s3m_url`, `s3m_token`. NERSC-side: `nersc_iri_url`, `nersc_iri_token`, `nersc_account`, `nersc_machine`, `nersc_remote_dir`. Other: `rag_db_path`, `rag_model`, `omd_url`, `omd_api_key`, `mcp_apps_dir`. Frontend uses `ui/.env.local` for `MCP_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`, etc. See `ui/README.md` for full list.

## Key Patterns

- All MCP tools are `async def` functions decorated with `@mcp.tool()`
- Docker sandbox mounts `skills/` as read-only and `data/output/` as writable
- The RAG pipeline (`build_rag.py`) indexes PDFs with citation extraction
- Frontend uses Tailwind CSS v4 and Next.js 16 App Router (file-based routing under `ui/app/`)
- HPC job submission has two backends: **Odo** uses the S3M API (`s3m_url`, `s3m_token`) for submit, and SSH/SCP to `hpc_host` for log/output download. **Perlmutter** uses the NERSC IRI API (`nersc_iri_url`, `nersc_iri_token`) for submit *and* file access via the amscrot SDK — no SSH session is opened to NERSC.
- A single persistent SSH connection (using `TTYSSHClient`) is created at server startup *for Odo only*; it prompts on `/dev/tty` if key auth is unavailable. Skipped entirely when only NERSC is configured.
- Job directories under `hpc_jobs/` must contain `job.slurm` (the script) and optionally `cluster_defaults.json`. `cluster_defaults.json` is keyed by cluster name (`odo`, `perlmutter`); a job opts in to a cluster by including the corresponding section. The Perlmutter section additionally requires `job.perlmutter.slurm` (and optionally `setup_perlmutter.sh` for `pre_launch`).
- `submit_hpc_job` elicits a confirmation checkbox before submitting; all other HPC tools operate silently
