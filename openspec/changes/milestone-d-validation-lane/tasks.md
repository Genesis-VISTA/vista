## 1. Marker and env-flag hygiene

- [ ] 1.1 Ensure all live / real-HPC tests use `@pytest.mark.live` and/or `@pytest.mark.hpc`
- [ ] 1.2 Skip unless `VISTA_RUN_LIVE=1` / `VISTA_RUN_HPC=1` as applicable
- [ ] 1.3 Confirm PR CI expression remains `not live and not hpc and not sandbox`
- [ ] 1.4 Clarify `test_campaign_live_e2e.py` gating/comments so “live” means opt-in / non-default

## 2. Golden prompt suite

- [ ] 2.1 Add `backend/tests/live/` or repo-root `evals/` with prompt → expected tool allowlist cases
- [ ] 2.2 Include at least one case per default seed project (`molten-salt`, `alloy-design`) if both remain seeded
- [ ] 2.3 Soft-assert preferred tools; do not hard-fail on exact final answer wording
- [ ] 2.4 Wire suite to `VISTA_RUN_LIVE=1` + configured `VISTA_BACKEND_MODEL`

## 3. Nightly CI skeleton

- [ ] 3.1 Add GitLab scheduled (or documented manual) job e.g. `nightly:validation`
- [ ] 3.2 Run evaluation-runbook agent-mode golden prompts with dry-run HPC
- [ ] 3.3 Run fault-recovery checks from the runbook
- [ ] 3.4 Ensure secrets exist only in scheduled pipeline variables
- [ ] 3.5 Document failure ownership and flake policy

## 4. Playwright smoke (schedule / manual only)

- [ ] 4.1 One flow: open app → select project → send message → observe tool card and/or elicitation modal
- [ ] 4.2 Keep selectors resilient (prefer role/text)
- [ ] 4.3 Do not add Playwright as a required MR CI job

## 5. Optional weekly real HPC

- [ ] 5.1 Document submitting `hpc_jobs/example` with real credentials
- [ ] 5.2 Mark `@pytest.mark.hpc`; enable only with `VISTA_RUN_HPC=1`
- [ ] 5.3 Prefer weekly schedule or manual runbook section if flaky
- [ ] 5.4 Record expected job id, terminal status, output fetchability (or cluster limits)

## 6. Runbook linkage + acceptance

- [ ] 6.1 Cross-link validation-lane docs with `docs/evaluation-runbook.md` and testing index
- [ ] 6.2 Confirm documented nightly command set and schedule (or manual equivalent)
- [ ] 6.3 Confirm PR CI remains hermetic; live/HPC failures do not block merges
- [ ] 6.4 Confirm at least one golden agent-mode path and one dry-run HPC path in the scheduled job
