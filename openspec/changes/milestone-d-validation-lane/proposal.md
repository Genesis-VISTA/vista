## Why

PR CI must stay hermetic, but the team still needs continuous validation with
live models, dry-run HPC, fault recovery, and a minimal UI smoke. Milestone D
formalizes a scheduled/manual validation lane that wraps the evaluation runbook
without blocking merges.

## What Changes

- Enforce `live` / `hpc` marker hygiene and env-flag skip gates
- Add nightly (or scheduled/manual) CI skeleton for agent-mode golden prompts + dry-run HPC
- Soft-assert golden prompt suite per default seed project
- Optional weekly real-HPC smoke for `hpc_jobs/example`
- Minimal Playwright smoke (schedule/manual only)
- Cross-link evaluation runbook; keep it as the operational how-to

## Capabilities

### New Capabilities

- `validation-lane`: Opt-in live/HPC validation, nightly automation wrapper, golden prompts, and UI smoke outside PR CI

### Modified Capabilities

- (none)

## Impact

- Backend live tests / `evals/` (or `backend/tests/live/`)
- GitLab scheduled pipeline variables (secrets never required for MR pipelines)
- Optional Playwright deps for scheduled smoke only
- `docs/evaluation-runbook.md` remains metrics how-to; this change owns automation wrap + pass/fail summary
- Depends on A–C preferred so hermetic coverage exists before live flake triage
