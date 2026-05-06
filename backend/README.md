# vista-backend

PydanticAI + FastAPI service that owns the chat agent loop, system prompts,
skill discovery, and MCP tool dispatch for VISTA.

The Next.js UI is a thin "Backend-for-Frontend" that proxies to this backend service.

## Run

```bash
cd backend
uv run --env-file ../.env vista-backend
```

By default it listens on `127.0.0.1:8001` and connects to the MCP server at
`http://127.0.0.1:8000/mcp`. Override via `VISTA_BACKEND_HOST`,
`VISTA_BACKEND_PORT`, `MCP_BASE_URL` (or the existing `VISTA_MCP_*` vars).
