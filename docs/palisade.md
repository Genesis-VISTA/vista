# PALISADE

PALISADE is the security-mediation sidecar the backend runs around VISTA's agent: it gates the
prompt, tool calls, RAG retrievals, sandboxed code, HPC jobs, and the citations and values in
the agent's answer. It replaced VISTAGuard, which used to be vendored in the backend as
`vista_backend.vistaguard`; that module, its `VISTA_BACKEND_VISTAGUARD__*` variables and its
`vistaguard-g4` extra are gone.

PALISADE is its own package, `palisade==1.0.0`, pinned to a tag of
`github.com/herronej/palisade_siege_agentic_security` in
[`backend/pyproject.toml`](../backend/pyproject.toml). The gates, their tiers and their
settings are documented there, in the package's `ARCHITECTURE.md` and `palisade/config.py`.
This page covers only how VISTA runs it.

**Everything is off by default.** With the master flag off, the sidecar builds no capabilities
and the agent runs exactly as it would without PALISADE.

## Getting the package

The repository is private until its licence clears review, so syncing the backend needs read
access to it: on a developer machine, a credential helper that already signs you in to GitHub.
GitLab CI uses `PALISADE_GITHUB_TOKEN`, a read-only token scoped to that repository (see
[`.gitlab-ci.yml`](../.gitlab-ci.yml)). Upgrading PALISADE is a change to the pinned tag, on
purpose: a gate stack that drifts underneath a deployment is a security change nobody reviewed.

## Turning it on

PALISADE's settings are a nested field of the backend's settings, so every field is set as
`VISTA_BACKEND_PALISADE__<FIELD>` (double underscore), in the environment or the repo-root
`.env`. They are read when the backend starts; restart it after a change.

```bash
export VISTA_BACKEND_PALISADE__ENABLED=true        # master flag
export VISTA_BACKEND_PALISADE__G1_ENABLED=true     # prompt
export VISTA_BACKEND_PALISADE__G2_ENABLED=true     # tool calls
export VISTA_BACKEND_PALISADE__G3_ENABLED=true     # RAG retrievals
export VISTA_BACKEND_PALISADE__G4_ENABLED=true     # sandbox code and file writes
export VISTA_BACKEND_PALISADE__G5_ENABLED=true     # HPC jobs
export VISTA_BACKEND_PALISADE__G6_ENABLED=true     # citations and values in the answer
export VISTA_BACKEND_PALISADE__QUARANTINE_ENABLED=true   # slow tiers (Q-LLM)
./launch.sh logs
```

Each gate can be turned on alone. G7 is deprecated: PALISADE merged it into G4, and
`G7_ENABLED` now turns on G4 with a warning. `SEMGREP_ENABLED=true` adds G4's Semgrep tier;
`semgrep` is an ordinary backend dependency, so there is nothing extra to install.

## What VISTA sets up around it

- **The slow tiers use the researcher's own model.** With `QUARANTINE_ENABLED`, VISTA builds
  the Q-LLM agents from the same model and credential as the chat agent, the one chosen in
  Settings › Agent, so PALISADE's `QUARANTINE_MODEL` has no effect in VISTA. For CUI or
  export-controlled work, choose an in-deployment endpoint there.
- **Paths.** PALISADE's `CONTRACTS_DIR` and `KNOWLEDGE_BASES_DIR` default to paths relative to
  the backend's working directory (`backend/` in a checkout), where neither exists, and VISTA
  does not set them. Point them at the repository's contracts and at VISTA's knowledge bases:

  ```bash
  export VISTA_BACKEND_PALISADE__CONTRACTS_DIR=../palisade_contracts
  export VISTA_BACKEND_PALISADE__KNOWLEDGE_BASES_DIR=../data/knowledge-bases
  ```

  Without them PALISADE runs with no domain contracts and skips G3's live corpus-integrity
  check. Release packages do not ship `palisade_contracts/`.
- **Domain contracts.** [`palisade_contracts/`](../palisade_contracts/) holds VISTA's
  molten-salt contracts, which G6 checks stated values against (`physical_bounds`,
  `data_value_contract`, `unit_consistency`, `correlation_form`, `provenance_binding`), and the
  G3 and G5 policy files (`g3_kb_policy.json`, `g5_allocation_policy.json`). The bounds are
  advisory surrogates until a domain scientist signs them off.
- **Hybrid retrieval (G3).** With `G3_HYBRID_RETRIEVAL=true`, the backend asks `rag_search` for
  BM25-plus-vector retrieval. Indexing a knowledge base writes its `bm25_corpus.json` beside the
  Chroma store, and builds it for an existing store that lacks one; a knowledge base without it
  falls back to vector-only retrieval with a warning.
- **Tool approval.** Tools PALISADE marks as needing approval are routed to the UI's approval
  dialog. `G5_REQUIRE_SUBMIT_APPROVAL` (off by default) holds `submit_hpc_job` there.
- **Trust state.** With the master flag on, the backend also serves
  `GET /projects/{project}/palisade/state` (the session's trust scores and tiers) and
  `POST /projects/{project}/palisade/reauth` (clears sticky lock-in after the researcher
  re-authenticates). With it off, those routes do not exist.

Uploads are not screened by PALISADE: the backend checks only their size and file names.

## Logs

PALISADE logs under `palisade.*` loggers, which `./launch.sh logs` sends to `logs/backend.log`.
A G1 denial also appears in the chat run as a `PALISADE:G1` warning, and the agent run stops
before the model is called.

```bash
grep -i palisade logs/backend.log | tail
```

Set `VISTA_BACKEND_PALISADE__PROVENANCE_LOG_PATH` to also write every gate decision and incident
as one JSON line per event, flushed after each write:

```bash
export VISTA_BACKEND_PALISADE__PROVENANCE_LOG_PATH=logs/palisade_provenance.jsonl
tail -f logs/palisade_provenance.jsonl | jq .
```

`FLOWCEPT_ENABLED` and `FLOWCEPT_ENDPOINT` send the same events to a Flowcept broker instead.
