## 1. Marker and env-flag hygiene

- [x] 1.1 Ensure all live / real-HPC tests use `@pytest.mark.live` and/or `@pytest.mark.hpc`
- [x] 1.2 Skip unless `VISTA_RUN_LIVE=1` / `VISTA_RUN_HPC=1` as applicable
- [x] 1.3 Confirm PR CI expression remains `not live and not hpc and not sandbox`
- [x] 1.4 Clarify `test_campaign_live_e2e.py` gating/comments so “live” means opt-in / non-default

## 2. Golden prompt suite

- [x] 2.1 Add `backend/tests/live/` or repo-root `evals/` with prompt → expected tool allowlist cases
- [x] 2.2 Include at least one case per default seed project (`molten-salt`, `alloy-design`) if both remain seeded
- [x] 2.3 Soft-assert preferred tools; do not hard-fail on exact final answer wording
- [x] 2.4 Wire suite to `VISTA_RUN_LIVE=1` + configured `VISTA_BACKEND_MODEL`

## 3. Nightly CI skeleton

- [x] 3.1 Add GitLab scheduled (or documented manual) job e.g. `nightly:validation`
- [x] 3.2 Run evaluation-runbook agent-mode golden prompts with dry-run HPC
- [x] 3.3 Run fault-recovery checks from the runbook
- [x] 3.4 Ensure secrets exist only in scheduled pipeline variables
- [x] 3.5 Document failure ownership and flake policy

## 4. Playwright smoke (schedule / manual only)

- [x] 4.1 One flow: open app → select project → send message → observe tool card and/or elicitation modal
- [x] 4.2 Keep selectors resilient (prefer role/text)
- [x] 4.3 Do not add Playwright as a required MR CI job

## 5. Optional weekly real HPC

- [x] 5.1 Document submitting `hpc_jobs/example` with real credentials
- [x] 5.2 Mark `@pytest.mark.hpc`; enable only with `VISTA_RUN_HPC=1`
- [x] 5.3 Prefer weekly schedule or manual runbook section if flaky
- [x] 5.4 Record expected job id, terminal status, output fetchability (or cluster limits)

## 6. Runbook linkage + acceptance

- [x] 6.1 Cross-link validation-lane docs with `docs/evaluation-runbook.md` and testing index
- [x] 6.2 Confirm documented nightly command set and schedule (or manual equivalent)
- [x] 6.3 Confirm PR CI remains hermetic; live/HPC failures do not block merges
- [x] 6.4 Confirm at least one golden agent-mode path and one dry-run HPC path in the scheduled job
