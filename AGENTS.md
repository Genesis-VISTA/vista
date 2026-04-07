# AGENTS.md

## Project Overview

VISTA (Visual Intelligence for Scientific & Tooling Assistant) is a scientific assistant for molten salt thermophysical properties. It combines a Python FastMCP server (tools for sandboxed execution, RAG search, HPC job submission) with a Next.js frontend that orchestrates LLM-driven tool calls.

## Common Commands

Starts both backend and frontend in tmux:
```bash
./launch.sh tmux
```

Launches Backend MCP server on :8000/mcp
```bash
cd mcp-server
uv run --env-file ../.env vista-mcp-server --transport=http
```

Launches Next.js Frontend dev server on :3000
```bash
cd ui
npm install
npm run dev
```

## Architecture
- `./mcp-server`
    - A MCP Server containing tools for sandboxed code execution, remote HPC job submission, and other tasks
- `./hpc_jobs`
    - Predefined jobs that the agent can submit to the remote HPC system
- `./skills`
    - Agent Skill files
- `./ui`
    - Frontend UI and agent loop that calls the tools in the mcp-server

## Config
Env is read from the root .env file.

MCP server config uses `pydantic-settings` with `VISTA_MCP_` env prefix (config in mcp-server/src/vista_mcp_server/config.py)
