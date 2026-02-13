# Vista UI

This UI is a Next.js App Router console for MCP skills and tool execution. It talks to the MCP server through the orchestrator routes under `ui/app/api` and never calls MCP directly from the browser.

## Run

Terminal 1: Start the MCP server over HTTP

```bash
python -m vista.app --transport http --host 127.0.0.1 --port 8000
# or, if using uv script entrypoint:
# uv run vista --transport http --host 127.0.0.1 --port 8000
```

Terminal 2: Start the UI

```bash
cd ui
npm install
npm run dev
```

The UI reads `MCP_BASE_URL` on the server side only. If not set, it defaults to `http://127.0.0.1:8000/mcp`.
Use `ui/.env.example` as the baseline:

```bash
MCP_BASE_URL=http://127.0.0.1:8000/mcp
```

## Repo Structure

```
project-root/
├─ clients/
├─ scripts/
├─ skills/
├─ src/
└─ ui/                  # Next.js App Router UI + orchestrator API routes
```
