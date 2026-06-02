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

As of **Phase 3.5**, gates are exposed to the agent as PydanticAI
`AbstractCapability` subclasses (`G1PromptCapability`…`G5HpcCapability`)
that register `before_run` / `before_model_request` / `before_tool_execute`
/ `after_tool_execute` hooks — the legacy `process_tool_call` composition
is retired. **Phase 5** adds the adaptive layer on top: per-capability
Bayesian trust scoring, sticky high-stakes lock-in, the SEV1/2/3 incident
playbook, the scientist-authored contract library, and a trust-state /
re-auth API. **Phase 7** ships provenance to a Flowcept broker.

### Configuration

VISTAGuard reads from `Settings.vistaguard` (declared in
`src/vista_backend/vistaguard/config.py`) and overrides via env vars
using the `VISTA_BACKEND_VISTAGUARD__<FIELD>` prefix — the double
underscore separates the parent field from the nested name.

| Env var | Default | Gate | Phase | What it does |
|---|---|---|---|---|
| `VISTA_BACKEND_VISTAGUARD__ENABLED` | `false` | — | 0 | Master toggle. When `false`, every capability hook short-circuits to a no-op and `is_active()` is False. |
| `VISTA_BACKEND_VISTAGUARD__G1_ENABLED` | `false` | G1 Prompt | 3 | Pre-LLM prompt check + trust-tier banner in the system prompt; enforces SEV1 session termination. |
| `VISTA_BACKEND_VISTAGUARD__G2_ENABLED` | `false` | G2 Tool | 1 | Tool-call gating via the `before_tool_execute` hook. |
| `VISTA_BACKEND_VISTAGUARD__G3_ENABLED` | `false` | G3 RAG / Memory | 2 | Sanitization + per-chunk tagging of `rag_search` returns; runs contracts over retrieved claims. |
| `VISTA_BACKEND_VISTAGUARD__G4_ENABLED` | `false` | G4 Code | 3 | Semantic-pattern (Semgrep) scanning of code emitted to `run_bash` / `create_file`. |
| `VISTA_BACKEND_VISTAGUARD__G5_ENABLED` | `false` | G5 HPC Job | 4 | Gating of `submit_hpc_job`; the fast tier consults the HPC contracts via the registry. |
| `VISTA_BACKEND_VISTAGUARD__QUARANTINE_ENABLED` | `false` | (slow-tier) | 1+ | Master slow-tier toggle. When `false`, every enabled gate runs fast-only (contracts still run — they need no model). |
| `VISTA_BACKEND_VISTAGUARD__QUARANTINE_MODEL` | `ollama:llama3.2:3b` | (slow-tier) | 1+ | PydanticAI model spec for the Q-LLM. Local serving keeps CUI content in-deployment. |
| `VISTA_BACKEND_VISTAGUARD__INITIAL_TRUST` | `1.0` | (trust) | 5 | Starting per-session trust score, in `[0, 1]`. |
| `VISTA_BACKEND_VISTAGUARD__STICKY_HIGH_STAKES` | `true` | (trust) | 5 | Sticky high-stakes lock-in: denied G5-above-ceiling / G3-CUI / destructive capabilities do not auto-recover within a session. Set `false` only for the oscillation-attack benchmark. |
| `VISTA_BACKEND_VISTAGUARD__CONTRACTS_DIR` | `../vistaguard_contracts` | (contracts) | 5 | Directory of operator-supplied `*.py` contracts loaded alongside the builtin salt/spectroscopy/HPC sets. |
| `VISTA_BACKEND_VISTAGUARD__FLOWCEPT_ENABLED` | `false` | (audit) | 7 | Ship provenance events to a Flowcept broker. When `false`, events go to the JSONL log instead. |
| `VISTA_BACKEND_VISTAGUARD__FLOWCEPT_ENDPOINT` | `null` | (audit) | 7 | Flowcept broker endpoint. Required when `FLOWCEPT_ENABLED=true`; the emitter raises `ValueError` at construction time if this is missing. |
| `VISTA_BACKEND_VISTAGUARD__PROVENANCE_LOG_PATH` | `null` | (audit) | 0 | JSONL log destination when Flowcept is disabled (and the broker fallback target). `null` routes events through the module logger. |

`tier_transition_thresholds` (defaults `{ELEVATED: 0.7, RESTRICTED: 0.4,
TERMINATED: 0.1}`) is a dict field; override it in `config.py` or via a
JSON-valued env var rather than a scalar.


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

### Phase 5: trust tiers, incidents, and contracts

Enabling the gates activates the adaptive layer automatically — no extra
flags are required. A representative "everything on" run:

```bash
VISTA_BACKEND_VISTAGUARD__ENABLED=true \
VISTA_BACKEND_VISTAGUARD__G1_ENABLED=true \
VISTA_BACKEND_VISTAGUARD__G2_ENABLED=true \
VISTA_BACKEND_VISTAGUARD__G3_ENABLED=true \
VISTA_BACKEND_VISTAGUARD__G4_ENABLED=true \
VISTA_BACKEND_VISTAGUARD__G5_ENABLED=true \
uv run vista-backend
```

What this turns on:

- **Per-capability Bayesian trust scoring.** Each gate keeps an independent
  trust posterior; clean tool calls restore that capability's trust while a
  violation degrades it. Trust never crosses capabilities, so a clean stream
  of tool calls can't wash out a RAG breach.
- **Incident playbook.** A gate incident routes through `IncidentManager`:
  **SEV3** logs only, **SEV2** elevates the session tier and forces re-auth,
  **SEV1** terminates the session (G1 / the agent loop then refuses every
  further request).
- **Sticky high-stakes lock-in** (`STICKY_HIGH_STAKES=true`, the default). A
  high-stakes denial does not auto-recover within the session; only an
  explicit re-auth clears it.
- **Contracts.** The builtin salt / spectroscopy / HPC contract library loads
  at startup (plus any `*.py` under `CONTRACTS_DIR`). G5's fast tier enforces
  the HPC contracts; the gate slow tiers run domain contracts over the claims
  in their context and record violations as incidents.

> ⚠️ The salt-property coefficient envelopes ship **advisory-pending domain-
> scientist sign-off** (`contracts/salts.py` carries a `REVIEW` marker). Review
> them before promoting any contract to a hard deny in a real deployment.

### Trust-state & re-auth API

When `ENABLED=true`, two endpoints are mounted (and documented in the OpenAPI
schema); they are absent entirely when VISTAGuard is off and reuse the normal
project authorization:

```bash
# Per-capability scores/tiers, sticky state, and domain-contract coverage
curl http://127.0.0.1:8001/projects/<name>/vistaguard/state

# Clear sticky high-stakes lock-in after a user re-authenticates
curl -X POST http://127.0.0.1:8001/projects/<name>/vistaguard/reauth
```

### Flowcept provenance (Phase 7)

By default, provenance events (gate decisions + incidents) are written to the
JSONL log (`PROVENANCE_LOG_PATH`) or the module logger. To ship them to a
Flowcept broker instead, install the optional dependency and set the endpoint:

```bash
# Install the optional Flowcept client
uv pip install -e '.[vistaguard-flowcept]'

VISTA_BACKEND_VISTAGUARD__ENABLED=true \
VISTA_BACKEND_VISTAGUARD__FLOWCEPT_ENABLED=true \
VISTA_BACKEND_VISTAGUARD__FLOWCEPT_ENDPOINT=redis://broker.local:6379 \
VISTA_BACKEND_VISTAGUARD__PROVENANCE_LOG_PATH=logs/vistaguard/provenance.jsonl \
uv run vista-backend
```

Notes:

- `FLOWCEPT_ENABLED=true` **requires** `FLOWCEPT_ENDPOINT`; the emitter
  fails fast at construction otherwise.
- The `flowcept` package is imported lazily and is optional — the backend runs
  without it unless Flowcept is enabled.
- Emission is resilient: if the broker is unreachable (or `flowcept` is not
  installed), the emitter logs once and **degrades to the JSONL log** for the
  rest of the session so a run is never blocked on provenance — set
  `PROVENANCE_LOG_PATH` to keep that fallback durable.
- Each event is shipped as a Flowcept task grouped under a per-session
  workflow; the sink is flushed on agent teardown.
