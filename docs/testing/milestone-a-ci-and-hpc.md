# Milestone A — CI truth + HPC contracts

## Objective

Every PR runs existing `vista_mcp_server` unit tests. The curated `hpc_jobs/`
catalog and the HPC submit path are validated **without** talking to a
cluster (dry-run + fakes).

**Status:** Implemented (this MR).

## In scope

- Wire `vista_mcp_server` into GitLab CI and `scripts/ci-local.sh`
- Register shared pytest markers; keep PR CI hermetic
- Catalog contract tests for every job under `hpc_jobs/`
- `FakeIriClient` / `FakeGlobusClient` + JobSpec construction unit tests
- Golden fixtures for IRI status / cancel parsing
- Expand dry-run lifecycle coverage where cheap

## Out of scope

- Real HPC / Globus / IRI network calls
- ProjectAgent / LLM loop (Milestone B)
- RAG, sandbox expansion, skills wiring (Milestone C)
- Nightly / live lanes (Milestone D)
- VISTAGuard (roadmap-wide out of scope)

## Dependencies

None. This is the first milestone.

## Work items

### 1. CI wiring

- [x] Add `vista-mcp:lint` and `vista-mcp:test` jobs to [`.gitlab-ci.yml`](../../.gitlab-ci.yml)
  - Mirror `dev-mcp:*` patterns (uv image, cache on `mcp_servers/vista_mcp_server/uv.lock`)
  - `vista-mcp:test` is **required** (not `allow_failure`)
  - Hermetic marker filter: `-m "not live and not hpc and not sandbox"`
  - `vista-mcp:lint` checks **`tests/` only** (src/ has pre-existing ruff debt)
- [x] Extend [`scripts/ci-local.sh`](../../scripts/ci-local.sh)
  - `mcp test` runs **both** `dev_mcp_server` and `vista_mcp_server`
  - `dev-mcp:test` remains advisory (`allow_failure` / non-`--strict`)
  - `vista-mcp:test` fails the script on failure (required)
- [x] `mcp` target covers both servers (no separate `vista-mcp` CLI target)

### 2. Pytest markers + defaults

- [x] Register markers in backend, `vista_mcp_server`, and `dev_mcp_server` `pyproject.toml`
- [x] Marker set: `unit`, `integration`, `live`, `sandbox`, `hpc`
- [x] [`test_campaign_live_e2e.py`](../../backend/tests/test_campaign_live_e2e.py)
  clarified as hermetic and marked `integration` (filename is historical; true
  opt-in `live` tests come in Milestone D)
- [x] Backend + vista-mcp CI use `-m "not live and not hpc and not sandbox"`

### 3. Catalog contract tests

- [x] [`tests/test_job_catalog.py`](../../mcp_servers/vista_mcp_server/tests/test_job_catalog.py)
  - Parametrized on-disk contract for all `hpc_jobs/*`
  - `get_available_jobs()` matches disk
  - Negative cases: missing README, no script, bad header

### 4. Fake IRI / Globus + JobSpec unit tests

- [x] [`tests/fakes/`](../../mcp_servers/vista_mcp_server/tests/fakes/) — `FakeIriClient`, `FakeGlobusClient`
- [x] [`tests/test_submit_job_spec.py`](../../mcp_servers/vista_mcp_server/tests/test_submit_job_spec.py)
  — Odo / Perlmutter / Frontier JobSpec assertions (Slurm inline, `VISTA_OUT`, sync)
- [x] Dry-run expanded for perlmutter/frontier + cancel-all-clusters in
  [`test_dry_run.py`](../../mcp_servers/vista_mcp_server/tests/test_dry_run.py)

### 5. Golden fixtures

- [x] [`tests/fixtures/iri_status_completed.json`](../../mcp_servers/vista_mcp_server/tests/fixtures/iri_status_completed.json)
- [x] [`tests/fixtures/iri_status_running.json`](../../mcp_servers/vista_mcp_server/tests/fixtures/iri_status_running.json)
- [x] Perlmutter status formatting covered in `test_perlmutter_status_formats_golden_fixture`

## Acceptance criteria

- [x] `./scripts/ci-local.sh mcp test` runs **vista_mcp** tests and fails if they fail
- [x] GitLab pipeline has a **required** `vista-mcp:test` job
- [x] Adding a broken entry under `hpc_jobs/` fails catalog tests
- [x] Submit-path unit tests pass with network disabled
- [x] Markers registered; hermetic CI defaults in place

## How to run

```bash
./scripts/ci-local.sh mcp test

cd mcp_servers/vista_mcp_server
uv sync --frozen --extra dev
uv run pytest tests/ -v --tb=short -m "not live and not hpc and not sandbox"
```

## Handoff to Milestone B

- Markers and hermetic CI defaults are in place for backend agent tests.
- HPC fakes are available if the agent harness wants a stub `submit_hpc_job`
  that mirrors dry-run summaries.
- Do **not** block B on expanding every JobSpec edge case; Odo + Perlmutter +
  Frontier happy paths are covered.
