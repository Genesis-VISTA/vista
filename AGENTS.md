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

## NextJS
ALWAYS read docs before coding

Before any Next.js work, find and read the relevant doc in `ui/node_modules/next/dist/docs/`. Your training data is outdated — the docs are the source of truth.
