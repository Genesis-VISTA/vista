# Milestone D — Validation lane (nightly / live)

## Objective

Formalize continuous validation **beyond** PR CI using the existing
[evaluation runbook](../evaluation-runbook.md): scheduled / manual live agent
runs, dry-run HPC paths, fault recovery, and a minimal UI smoke. PR pipelines
stay hermetic; live failures never block merges.

**Status:** Planned — not started; blocked on C by preference, not hard
dependency.

## In scope

- Marker hygiene (`live` / `hpc`) + env-flag skip gates
- Nightly (or scheduled) CI skeleton + documented command set
- Golden prompt suite (soft asserts on expected tools)
- Optional weekly real-HPC smoke for `hpc_jobs/example`
- Thin Playwright smoke (send message → tool or elicitation UI)
- Pass/fail criteria linking to the evaluation runbook

## Out of scope

- Making PR CI depend on AmSC API keys or cluster credentials
- Expanding campaign unit/e2e coverage (already strong — only mark/skip hygiene)
- **VISTAGuard** red-team / security gate suites (roadmap-wide out of scope)
- Replacing the evaluation runbook — D **wraps** it; the runbook remains the
  operational how-to for metrics collection

## Dependencies

- Milestones A–C preferred so hermetic coverage exists before investing in live
  flakiness triage.
- Markers from A; agent harness from B useful for documenting expected tools.

## Work items

### 1. Marker hygiene

- [ ] Ensure all live / real-HPC tests use `@pytest.mark.live` and/or
  `@pytest.mark.hpc`
- [ ] Skip unless env flags set:

  | Flag | Enables |
  |------|---------|
  | `VISTA_RUN_LIVE=1` | Live LLM / agent-mode tests |
  | `VISTA_RUN_HPC=1` | Real cluster submit smoke |

- [ ] PR CI continues: `-m "not live and not hpc and not sandbox"`
- [ ] Confirm [`test_campaign_live_e2e.py`](../../backend/tests/test_campaign_live_e2e.py)
  is correctly gated (even if it fakes MCP — treat as `live` or rename/clarify
  in comments so “live” means “opt-in / non-default”)

### 2. Nightly job doc + CI skeleton

- [ ] Add a GitLab **scheduled** pipeline job (or a documented manual job) that:
  - [ ] Runs evaluation-runbook **agent-mode** golden prompts with **dry-run HPC**
  - [ ] Runs fault-recovery checks from the runbook
  - [ ] Optionally runs Playwright smoke (below)
- [ ] Secrets (AmSC inference key, optional HPC tokens) exist **only** in the
  scheduled pipeline variables — never required for MR pipelines
- [ ] Document failure ownership (who gets notified; flake policy)

Suggested CI job name: `nightly:validation` (stage `nightly` or `test` with
`rules: - if: $CI_PIPELINE_SOURCE == "schedule"`).

### 3. Golden prompt suite

- [ ] Add `backend/tests/live/` or repo-root `evals/` with checked-in cases:

  ```text
  prompt → expected tool-name allowlist (soft assert)
  ```

- [ ] At least one case per default project (`molten-salt`, `alloy-design`) if
  both remain seeded
- [ ] Soft assert: preferred tools called; do not hard-fail on exact wording of
  the final answer unless a separate rubric is added later
- [ ] Wire to `VISTA_RUN_LIVE=1` + configured `VISTA_BACKEND_MODEL`

### 4. Optional weekly HPC smoke

- [ ] Document submitting [`hpc_jobs/example`](../../hpc_jobs/example) with real
  credentials
- [ ] Mark `@pytest.mark.hpc`; enable only when `VISTA_RUN_HPC=1`
- [ ] **Not** part of default nightly if flaky; prefer a separate weekly schedule
  or manual runbook section
- [ ] Record expected: job id returned, status reaches terminal state, outputs
  fetchable (or clearly document cluster-specific limits)

### 5. Playwright smoke (minimal)

- [ ] One flow: open app → select project → send message → observe tool card
  and/or elicitation modal
- [ ] Runs only on schedule / manual; not in MR CI
- [ ] Keep selectors resilient; prefer role/text over brittle CSS

### 6. Link evaluation runbook

- [ ] This milestone owns the **automation wrapper** and pass/fail summary
- [ ] [`docs/evaluation-runbook.md`](../evaluation-runbook.md) remains the
  step-by-step for metrics JSONL, amortization, concurrency curves, etc.
- [ ] Cross-link from [README.md](./README.md) and the runbook (“Automated via
  Milestone D nightly”)

## Acceptance criteria

- [ ] Documented nightly command set and CI schedule (or manual equivalent)
- [ ] PR CI remains hermetic; live / HPC failures do not block merges
- [ ] At least one golden agent-mode path automated
- [ ] At least one dry-run HPC path exercised in the scheduled job
- [ ] Env-flag skip gates documented and enforced

## How to run

```bash
# Hermetic (default) — what developers and MR CI run
./scripts/ci-local.sh test

# Live agent golden suite (needs model + API key)
export VISTA_RUN_LIVE=1
# also: OPENAI_API_KEY, VISTA_BACKEND_MODEL, stack up if required
cd backend && uv run pytest tests/live/ -v -m live

# Real HPC smoke (needs tokens + Globus env as applicable)
export VISTA_RUN_HPC=1
cd mcp_servers/vista_mcp_server && uv run pytest -v -m hpc
```

Follow [evaluation-runbook.md](../evaluation-runbook.md) for metrics collection
phases (tools mode vs agent mode).

## Sequencing

1. Marker + env-flag hygiene
2. Golden prompt suite (local-runable with secrets)
3. Scheduled CI job skeleton (dry-run HPC + agent mode)
4. Playwright smoke
5. Optional weekly real-HPC documentation / job

## Handoff / maintenance

- After D lands, new live tests must declare markers and env gates.
- Flaky live tests are quarantined or moved to weekly — they must not erode
  trust in MR CI.
- Next roadmap after D is product-driven (new domains / jobs), not more
  foundation testing — unless coverage gaps reopen when features ship.
