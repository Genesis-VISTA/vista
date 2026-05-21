# vista-backend

PydanticAI + FastAPI service that owns the chat agent loop, system prompts,
skill discovery, and MCP tool dispatch for VISTA.

The Next.js UI is a thin "Backend-for-Frontend" that proxies to this backend service.

## Run

```bash
cd backend
uv run vista-backend
```

## Development
This is in early development, see [new_backend_plan.md](./docs/new_backend_plan.md) for more info.

## VISTAGuard sidecar

VISTAGuard is the security-mediation sidecar that wraps `ProjectAgent`'s
tool-call surface with a chain of fast-then-slow gates. The
sidecar is a modular addition: with the master flag off (the default),
every runtime hook is a byte-identical no-op against baseline VISTA.

### Configuration

VISTAGuard reads from `Settings.vistaguard` (declared in
`src/vista_backend/vistaguard/config.py`) and overrides via env vars
using the `VISTA_BACKEND_VISTAGUARD__<FIELD>` prefix — the double
underscore separates the parent field from the nested name.

| Env var | Default | Gate | Phase | What it does |
|---|---|---|---|---|
| `VISTA_BACKEND_VISTAGUARD__ENABLED` | `false` | — | 0 | Master toggle. When `false`, `VistaGuardSidecar.process_tool_call` short-circuits to the caller hook unchanged and `is_active()` is False. |
| `VISTA_BACKEND_VISTAGUARD__G1_ENABLED` | `false` | G1 Prompt | 3 | Pre-LLM prompt check + tier banner in the system prompt. |
| `VISTA_BACKEND_VISTAGUARD__G2_ENABLED` | `false` | G2 Tool | 1 | Tool-call gating inside `process_tool_call`. |
| `VISTA_BACKEND_VISTAGUARD__G3_ENABLED` | `false` | G3 RAG / Memory | 2 | Sanitization of `rag_search` returns. |
| `VISTA_BACKEND_VISTAGUARD__G4_ENABLED` | `false` | G4 Code | 3 | Semantic-pattern scanning of code emitted to `run_bash` / `create_file`. |
| `VISTA_BACKEND_VISTAGUARD__G5_ENABLED` | `false` | G5 HPC Job | 4 | Gating of `submit_hpc_job` and related HPC tool calls. |
| `VISTA_BACKEND_VISTAGUARD__QUARANTINE_ENABLED` | `false` | (slow-tier) | 1+ | Master slow-tier toggle. When `false`, every enabled gate runs fast-only. |
| `VISTA_BACKEND_VISTAGUARD__QUARANTINE_MODEL` | `ollama:llama3.2:3b` | (slow-tier) | 1+ | PydanticAI model spec for the Q-LLM. Local serving keeps CUI content in-deployment. |
| `VISTA_BACKEND_VISTAGUARD__FLOWCEPT_ENABLED` | `false` | (audit) | 7 | Ship provenance events to a Flowcept broker. When `false`, events go to the JSONL log instead. |
| `VISTA_BACKEND_VISTAGUARD__FLOWCEPT_ENDPOINT` | `null` | (audit) | 7 | Flowcept broker endpoint. Required when `FLOWCEPT_ENABLED=true`; the emitter raises `ValueError` at construction time if this is missing. |
| `VISTA_BACKEND_VISTAGUARD__PROVENANCE_LOG_PATH` | `null` | (audit) | 0 | JSONL log destination when Flowcept is disabled. `null` routes events through the module logger. |


### Running locally with a gate enabled

The master flag and at least one per-gate flag must be on for the
sidecar to do anything:

```bash
# Enable the tool gate against the dev MCP server
VISTA_BACKEND_VISTAGUARD__ENABLED=true \
VISTA_BACKEND_VISTAGUARD__G2_ENABLED=true \
uv run vista-backend
```

To exercise the slow-tier Q-LLM path, point at a local Ollama or
vLLM endpoint and flip the quarantine flag:

```bash
VISTA_BACKEND_VISTAGUARD__ENABLED=true \
VISTA_BACKEND_VISTAGUARD__G2_ENABLED=true \
VISTA_BACKEND_VISTAGUARD__QUARANTINE_ENABLED=true \
VISTA_BACKEND_VISTAGUARD__QUARANTINE_MODEL=ollama:llama3.2:3b \
uv run vista-backend
```

To capture audit events to a file instead of the module logger:

```bash
VISTA_BACKEND_VISTAGUARD__ENABLED=true \
VISTA_BACKEND_VISTAGUARD__PROVENANCE_LOG_PATH=logs/vistaguard/provenance.jsonl \
uv run vista-backend
```
