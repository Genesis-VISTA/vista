# Vista UI

Next.js App Router BFF (Backend-for-Frontend) for the VISTA backend service.

The browser never calls the backend or MCP directly. All calls flow through route
handlers (`ui/app/api/*`) or Next.js server actions (`ui/app/actions/*`).

## What This UI Supports

- Chat with the VISTA agent via the Vercel AI SDK (`@ai-sdk/react`)
- Browse and manage projects and skills backed by the Python backend
- Upload and manage datasets
- Check MCP connectivity and discover available tools
- MCP elicitation: surfaced inline in the chat stream, answered via `/api/mcp/elicitation`

## Prerequisites

- Node.js 18+ (20+ recommended)
- Python backend running (see `backend/`)

## First-Time Setup (After Clone)

1. Install UI dependencies:

```bash
cd ui
npm install
```

2. Create local UI env file:

```bash
cp .env.example .env.local
```

3. Edit `ui/.env.local`. Minimum:

```bash
VISTA_BACKEND_URL=http://127.0.0.1:8001
```

## Run Locally

Terminal 1 — start the Python backend:

```bash
cd backend
uv run vista-backend
```

Terminal 2 — start the MCP server:

```bash
cd mcp-server
uv run vista-mcp-server --transport=http
```

Terminal 3 — start the UI:

```bash
cd ui
npm run dev
```

Open:

```text
http://localhost:3000
```

## Quick Test Plan

1. MCP health:

```bash
curl http://localhost:3000/api/mcp/health
```

Expected:
- `ok: true`

2. MCP tools:

```bash
curl http://localhost:3000/api/mcp/tools
```

Expected:
- tool list including `bash`

3. Chat (requires backend running):

```bash
curl -X POST http://localhost:3000/api/chat \
  -H "content-type: application/json" \
  -d '{"project_name":"<your-project>","messages":[{"role":"user","content":"hello"}]}'
```

Expected:
- Vercel AI SDK v5 streaming response

## Route Inventory

| Route | Purpose |
|---|---|
| `POST /api/chat` | Proxy to `VISTA_BACKEND_URL/projects/{name}/agent/run/vercel` |
| `POST /api/mcp/elicitation` | Forward elicitation answer to backend |
| `GET /api/mcp/health` | Proxy MCP reachability check to backend |
| `GET /api/mcp/tools` | Proxy MCP tool list from backend |
| `GET/POST /api/uploads/*` | Upload management proxied to backend |

Projects, skills, and uploads also use **server actions** in `app/actions/`.

## Troubleshooting

- `Address already in use` on port 8001:

```bash
lsof -nP -iTCP:8001 -sTCP:LISTEN
kill <PID>
```

- `ok: false` from `/api/mcp/health`:
  - verify `VISTA_BACKEND_URL` in `ui/.env.local` points to the running backend
  - check that both the backend service and MCP server are running

- Chat returns 400 `project_name is required`:
  - the frontend must include `project_name` in the request body

## Repo Structure

```text
project-root/
├─ backend/          # FastAPI + PydanticAI agent service (port 8001)
├─ mcp-server/       # FastMCP server (port 8000)
├─ skills/
└─ ui/
   ├─ app/
   │  ├─ actions/    # Server actions (projects, skills, uploads)
   │  └─ api/        # Route handlers (chat, mcp/*, uploads/*)
   ├─ components/
   ├─ lib/
   └─ README.md
```
