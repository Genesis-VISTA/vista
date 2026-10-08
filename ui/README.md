# Vista UI

Next.js App Router frontend for VISTA. It holds no agent loop of its own: the
chat, projects, skills, knowledge bases and Hypothesis Lab pages call
`app/api/*` route handlers, which proxy to the backend (FastAPI + PydanticAI,
see [`backend/`](../backend/)). The browser never calls the backend or an MCP
server directly.

Before changing anything here, read the relevant guide in
`node_modules/next/dist/docs/`: this is Next.js 16, and the docs that ship with
it are the source of truth.

## Run

The UI needs the backend, and the backend needs the MCP server. Start all three,
and the VISTA window, from the repo root with `./launch.sh logs`; see
[docs/development.md](../docs/development.md). To run only the UI against
services that are already up:

```bash
cd ui
npm install
npm run dev
```

It reads the repo-root `.env`. `VISTA_BACKEND_URL` (default
`http://127.0.0.1:8001`) is where it finds the backend, and `VISTA_MCP_URL`
(default `http://127.0.0.1:8000/mcp`) the MCP server for the `/api/mcp/*`
diagnostics routes. Inference keys are not
UI settings: researchers enter them in Settings › Agent, and the backend holds
them.

```bash
curl http://localhost:3000/api/mcp/health   # smoke test
```

## Test

```bash
npm run lint        # ESLint and the colour-token check
npm run typecheck
npm test            # Vitest component tests
npm run test:e2e:hermetic   # Playwright with every /api call stubbed in the page
```

`./scripts/ci-local.sh ui` runs the same set as CI. It builds into `ui/.next`,
so it stops a `./launch.sh` stack running from the same checkout.
