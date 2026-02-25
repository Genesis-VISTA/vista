# Vista UI

Next.js App Router tool console and orchestrator for the VISTA MCP backend.

The browser never calls MCP directly. All calls flow through `ui/app/api/*` route handlers.

## What This UI Supports

- Browse local skills from `project-root/skills/**/SKILL.md`
- Run MCP tools through `/api/mcp/call`
- Check MCP connectivity and discover tools
- Optional advisory LLM chat (`/api/chat`) with manual tool execution only
- Salt analysis quick action (`bash`) showing the generated command string

## Prerequisites

- Python 3.12+
- Node.js 18+ (or 20+ recommended)
- `uv` installed

## First-Time Setup (After Clone)

1. Install Python dependencies (repo root):

```bash
uv venv --python=3.12 .venv
source .venv/bin/activate
uv pip install -e .[dev]
```

2. Install UI dependencies:

```bash
cd ui
npm install
```

3. Create local UI env file:

```bash
cp .env.example .env.local
```

4. Edit `ui/.env.local` as needed. Minimum:

```bash
MCP_BASE_URL=http://127.0.0.1:8000/mcp
```

Optional LLM settings:

```bash
OPENAI_API_KEY=changeme
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_STYLE=responses
OPENAI_CHAT_URL=
OPENAI_AUTH_MODE=bearer
OPENAI_TIMEOUT_MS=30000
```

Azure example:

```bash
OPENAI_API_KEY=<azure_key>
OPENAI_API_STYLE=chat_completions
OPENAI_AUTH_MODE=api_key
OPENAI_CHAT_URL=https://<resource>.openai.azure.com/openai/deployments/<deployment>/chat/completions?api-version=2025-01-01-preview
OPENAI_TIMEOUT_MS=30000
```

## Run Locally

Terminal 1 (repo root): start MCP server over HTTP

```bash
python3 -m vista.app --transport http --host 127.0.0.1 --port 8000
# or:
# uv run vista --transport http --host 127.0.0.1 --port 8000
```

Terminal 2:

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
- tool list including `display_file`, `bash`

3. Salt analysis command pass-through:

```bash
curl -X POST http://localhost:3000/api/mcp/call \
  -H "content-type: application/json" \
  -d '{"tool":"bash","args":{"command":"skills/salt-analysis/scripts/analyze_salt.py --salt AlCl3-KCl"}}'
```

Expected:
- response echoes the command string passed to `bash`

4. LLM chat (if API key configured):

```bash
curl -X POST http://localhost:3000/api/chat \
  -H "content-type: application/json" \
  -d '{"message":"What MCP tools are available?"}'
```

## Troubleshooting

- `Address already in use` on port 8000:
```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
kill <PID>
```

- MCP returns `Missing session ID` or `Not Acceptable`:
  - use UI routes (`/api/mcp/*`) instead of calling raw MCP endpoint from browser
  - UI orchestrator handles session + accept headers

- `/api/chat` timeout:
  - increase `OPENAI_TIMEOUT_MS` in `ui/.env.local` (for example `30000`)

## Repo Structure

```text
project-root/
├─ clients/
├─ skills/
├─ src/
└─ ui/
   ├─ app/
   ├─ components/
   ├─ lib/
   └─ README.md
```
