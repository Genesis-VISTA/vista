# Validation lane (Milestone D)

Scheduled / manual live validation that wraps the
[evaluation runbook](evaluation-runbook.md). **PR CI stays hermetic** — this
lane never blocks merges.

| Lane | When | Secrets | Blocks merges? |
|------|------|---------|----------------|
| Hermetic PR CI | every MR | none | yes (required jobs) |
| Nightly validation | GitLab schedule or `./scripts/nightly-validation.sh` | schedule vars only | **no** |
| Weekly real HPC | weekly schedule / manual | HPC tokens | **no** |
| Playwright smoke | schedule / manual | none beyond running UI | **no** |

OpenSpec: [`openspec/changes/milestone-d-validation-lane/`](../openspec/changes/milestone-d-validation-lane/).

## Marker and env-flag gates

| Marker | Env flag | Purpose |
|--------|----------|---------|
| `@pytest.mark.live` | `VISTA_RUN_LIVE=1` | Real model / live services |
| `@pytest.mark.hpc` | `VISTA_RUN_HPC=1` | Real cluster submit |
| `@pytest.mark.sandbox` | (daemon present) | Microsandbox / Docker |

PR and `./scripts/ci-local.sh test` keep:

```text
-m "not live and not hpc and not sandbox"
```

Bare `pytest` without that filter still skips `live` / `hpc` unless the env
flags are set (hooks in each package's `tests/conftest.py`).

> **Filename note:** `backend/tests/test_campaign_live_e2e.py` is hermetic
> (`integration`). Real live tests live under `backend/tests/live/`.

## Nightly command set

With the stack running (`./launch.sh`) and dry-run HPC + a real model:

```bash
export VISTA_BACKEND_MODEL="anthropic:claude-sonnet-4-6"   # or your provider
export ANTHROPIC_API_KEY=...                                 # provider key
export VISTA_MCP_HPC_DRY_RUN=true                            # on MCP process
export VISTA_RUN_LIVE=1
export VISTA_LIVE_BASE_URL=http://127.0.0.1:8001
./scripts/nightly-validation.sh
```

The script runs:

1. **Golden agent-mode prompts** — soft tool allowlists per seed project
   (`molten-salt`, `alloy-design`) via `backend/tests/live/test_golden_prompts.py`
2. **Dry-run HPC** — `loadgen.py --campaigns 2 --poll` (tools mode)
3. **Fault checks** (optional) — `VISTA_RUN_FAULT_CHECKS=1` after starting MCP
   with `VISTA_MCP_FAULT__SUBMIT_FAIL_P=0.2` (see runbook fault-recovery note)
4. **Playwright** (optional) — `VISTA_RUN_PLAYWRIGHT=1`

### GitLab schedule

Job: `nightly:validation` in [`.gitlab-ci.yml`](../.gitlab-ci.yml).

- Rules: `$CI_PIPELINE_SOURCE == "schedule"` (or manual web trigger with
  `VISTA_RUN_VALIDATION=1`)
- `allow_failure: true` — flake never blocks the default branch health signal
  the way required MR jobs do
- **Secrets** (AmSC / provider keys, optional HPC tokens) MUST live only in
  **scheduled pipeline variables**, never in MR-protected variables required by
  merge pipelines

Recommended schedule variables:

| Variable | Example |
|----------|---------|
| `VISTA_BACKEND_MODEL` | `anthropic:claude-sonnet-4-6` |
| `ANTHROPIC_API_KEY` / provider key | (masked) |
| `VISTA_LIVE_BASE_URL` | URL of a standing validation stack |
| `VISTA_RUN_LIVE` | `1` |
| `VISTA_RUN_FAULT_CHECKS` | `1` (optional) |
| `VISTA_RUN_PLAYWRIGHT` | `1` (optional) |

## Failure ownership and flake policy

| Failure class | Action |
|---------------|--------|
| Golden soft-assert (wrong tools) | Open issue; triage model/prompt drift; do not gate MRs |
| Dry-run HPC / loadgen | Check MCP dry-run flag and stack health |
| Live flake / provider outage | Quarantine or move to weekly; keep `allow_failure` |
| Real HPC | Prefer weekly / manual; document cluster limits |
| Playwright | Schedule-only; never add as a required MR job |

**Owners:** assign nightly failure notifications to the VISTA maintainers
channel (exact Slack/GitLab target is an open ops choice — update this
paragraph when chosen).

## Golden prompt suite

`backend/tests/live/test_golden_prompts.py` maps prompts → preferred tool
allowlists. Soft assert: at least one preferred tool was called. Exact final
answer wording is ignored.

## Playwright smoke (schedule / manual)

```bash
cd ui
export PLAYWRIGHT_BASE_URL=http://127.0.0.1:3000
npx playwright test -c playwright.config.ts
```

Flow: open app → open Projects → activate a project → send a chat message →
observe a tool-call bubble and/or elicitation modal. Selectors prefer
role/text. **Not** part of required MR CI.

## Weekly / manual real HPC (`hpc_jobs/example`)

```bash
# MCP must NOT have VISTA_MCP_HPC_DRY_RUN set
export VISTA_RUN_HPC=1
export VISTA_HPC_SMOKE_CLUSTER=odo          # or frontier
export VISTA_HPC_SMOKE_PROJECT=molten-salt
# User needs S3M / IRI tokens configured (UI → User settings)
cd backend && uv run pytest tests/live/test_hpc_example_smoke.py -v -m hpc
```

### Expected outcomes

| Signal | Expectation |
|---------|-------------|
| Job id | Real id (not `dry-…`) |
| Terminal status | `COMPLETED` / `FAILED` / `CANCELLED` / … within timeout |
| Outputs | `get_hpc_job_outputs` may succeed or hit cluster limits — recorded in test output |

Job templates: [`hpc_jobs/example/`](../hpc_jobs/example/). Prefer a **weekly**
GitLab schedule (or manual) rather than default nightly if the facility is flaky.

## Testing index

| Doc / path | Role |
|------------|------|
| This page | Validation-lane automation + pass/fail policy |
| [evaluation-runbook.md](evaluation-runbook.md) | Metrics JSONL, amortization, concurrency how-to |
| [openspec/specs/testing-ci/spec.md](../openspec/specs/testing-ci/spec.md) | Hermetic PR CI contract |
| `./scripts/ci-local.sh` | Local mirror of required CI |
| `./scripts/nightly-validation.sh` | Local / scheduled validation wrapper |
