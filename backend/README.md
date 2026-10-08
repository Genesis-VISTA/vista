# vista-backend

PydanticAI + FastAPI service that owns the chat agent loop, system prompts,
projects, skills, knowledge bases, the Hypothesis Lab, and MCP tool dispatch
for VISTA. Its database is SQLite (`vista.db` in the data directory).

The Next.js UI is a thin "Backend-for-Frontend" that proxies to this backend service.

## Run

```bash
cd backend
uv run vista-backend
```

It listens on `127.0.0.1:8001` and reads the repo-root `.env`; settings use the
`VISTA_BACKEND_` prefix, with `__` separating nested fields (see
[`config.py`](src/vista_backend/config.py) and [`.env.sample`](../.env.sample)).
To run it with the other services, see [docs/development.md](../docs/development.md).

Syncing fetches PALISADE, the security sidecar, from its public GitHub repository at a pinned
tag; see [docs/palisade.md](../docs/palisade.md) for its licence status and the
`VISTA_BACKEND_PALISADE__*` settings.

## Test

```bash
cd backend
uv run --extra dev pytest -vv
```

Hermetic CI runs `-m "not live and not hpc and not sandbox"`.
