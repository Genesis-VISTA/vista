## Context

Milestones A–C establish hermetic coverage. Live validation already has an
operational runbook (`docs/evaluation-runbook.md`) but lacks a formal scheduled
wrapper, env-flag hygiene, and clear “never block merges” contract.

## Goals / Non-Goals

**Goals:**

- Marker + env-flag hygiene for `live` / `hpc`
- Scheduled/manual nightly skeleton wrapping agent-mode + dry-run HPC
- Soft-assert golden prompts; optional weekly real HPC; minimal Playwright smoke
- Preserve hermetic PR CI

**Non-Goals:**

- Making PR CI depend on AmSC keys or cluster credentials
- Expanding campaign unit coverage beyond mark/skip hygiene
- VISTAGuard red-team / security gate suites
- Replacing the evaluation runbook

## Decisions

1. **Env flags `VISTA_RUN_LIVE=1` and `VISTA_RUN_HPC=1`**
   - Rationale: explicit opt-in; hard to accidentally run expensive/live tests.

2. **Scheduled GitLab job `nightly:validation` (or documented manual equivalent)**
   - Rationale: secrets only in schedule variables; MR pipelines stay clean.

3. **Soft asserts on tool allowlists**
   - Rationale: live model wording drifts; tool selection is the stable signal.

4. **Playwright schedule-only**
   - Rationale: UI E2E flake must not erode MR CI trust.

5. **Real HPC weekly/manual, not default nightly if flaky**
   - Rationale: cluster availability and token scope vary.

## Risks / Trade-offs

- [Live flake erodes trust] → Quarantine or move to weekly; never gate merges
- [Secret leakage into MR variables] → Document ownership; CI rules enforce schedule-only
- [Filename confusion e.g. `test_campaign_live_e2e.py`] → Clarify markers/comments so “live” means opt-in

## Migration Plan

Sequence: marker hygiene → golden suite (local with secrets) → scheduled job →
Playwright → optional weekly HPC docs/job. After landing, new live tests must
declare markers and env gates.

## Open Questions

- Exact GitLab stage name (`nightly` vs `test` + schedule rules)
- Notification target for nightly failures (channel / owners)
