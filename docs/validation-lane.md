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
| VISTA window | manual: macOS or a Linux desktop; tests also in Docker | none | **no** |

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
   (`molten-salt`, `alloy-design`) via `backend/tests/live/test_golden_prompts.py`.
   Those two are science projects, so the deployment under test must be seeded
   with `VISTA_BACKEND_SEED_SCIENCE_PROJECTS=true` (a fresh default seed has only
   `ai-safety-autonomous-labs`)
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

Flow: open app → open Projects → activate the default
`ai-safety-autonomous-labs` project → send a chat message asking for a
`rag_search` over the AI-safety papers → observe a tool-call bubble and/or
elicitation modal. Selectors prefer role/text. **Not** part of required MR CI.

## VISTA window (manual)

The window's routing rules run in PR CI (`electron:test`). Its behaviour in a
real window needs a display, so it is checked here instead.

**Window tests** (fixture server, no services, ~10 s):

```bash
cd electron && npm ci && npx --no install-electron && npm run test:e2e
```

The same tests on Linux, in a container on any machine with Docker. The image matches the
pinned `@playwright/test`. It runs as root, so the sandboxed PDF case is skipped and the
`@no-sandbox` cases cover Linux's unsandboxed path. Behind TLS inspection, add
`-v ~/root-ca.pem:/ca.pem:ro -e NODE_EXTRA_CA_CERTS=/ca.pem`, or Electron's download fails.

```bash
cd electron && docker run --rm -v "$PWD:/w" -v /w/node_modules -w /w \
  mcr.microsoft.com/playwright:v1.62.1-noble sh -c 'npm ci && npx --no install-electron && xvfb-run -a npm run test:e2e'
```

This covers external links and `window.open` going to the system browser, off-origin
navigation and redirects being refused, `file:` links, same-origin pop-ups and PDFs
opening child windows, downloads, the page having no Node access, the single-instance
lock, startup progress and retry, startup-to-main handoff, shutdown cleanup, and
`--smoke-test` exit codes.

### Thin macOS developer app

This validates the source-backed app separately from the complete release
package:

```bash
./scripts/build_mac_dev_app.sh
codesign --verify --deep --strict "dist/mac-dev/VISTA Dev.app"
open "dist/mac-dev/VISTA Dev.app"
```

Confirm the preparation window appears without Terminal, transitions to the
1280 × 860 main window at `http://localhost:3000`, and exposes DevTools in the
View menu. Quit the app and confirm ports 3000, 8000, and 8001 are released.
The app is intentionally tied to the checkout named by `dist/mac-dev/dev-root`;
it does not exercise release payload assembly, relocation, notarization, or
Gatekeeper.

The checkout launcher has a hermetic lifecycle test:

```bash
./scripts/tests/mac_dev_launcher_test.sh
```

### Signed macOS release walk-through

Run this lane manually on the final archive produced with a `Developer ID
Application` identity and Apple notary credentials. Use a fresh macOS account
or machine. Do not clear quarantine, override Gatekeeper, or use Open Anyway;
needing any of those is a release failure.

1. Download the archive through a browser so it receives quarantine, verify its
   SHA-256 sidecar, and extract it normally. Confirm the whole folder remains
   together and `xattr -p com.apple.quarantine VISTA.app` reports quarantine.
2. Before launch, require all of these to pass:

   ```bash
   codesign --verify --deep --strict --verbose=4 VISTA.app
   xcrun stapler validate VISTA.app
   spctl --assess --type execute --verbose=4 VISTA.app
   ```

   Record the displayed Developer ID team. Locate the bundled `msb`, dump its
   entitlements with `codesign -d --entitlements :-`, and confirm both
   `com.apple.security.hypervisor` and
   `com.apple.security.cs.disable-library-validation` remain true.
3. Double-click `VISTA.app`. Confirm no Terminal window appears; first-run
   resource installation, sandbox import, service startup, and the handoff to
   the main interface are all visible in the app. Start one agent session so a
   real sandbox is created, and run the bundled `msb doctor` as an additional
   host check.
4. Quit with Cmd-Q. Confirm no package process or listener remains, then launch
   again immediately. The second run must visibly mark prepared work as skipped
   and reach the main window.
5. While VISTA is starting, launch `VISTA.app` again. Repeat after the main
   window appears. Each time the existing window comes forward and only one
   service stack exists.
6. Exercise an expected error by occupying port 3000 before launch. Confirm the
   graphical error names the conflict, Open Logs works, and Retry stays disabled
   until cleanup finishes. Release the port, select Retry, and confirm startup
   succeeds.
7. Copy only `VISTA.app` out of the folder and open the copy. It must show the
   graphical package-layout error and start no service. Delete the copy and
   leave the original package intact.
8. Close the startup window during one run and the main window during another.
   After every quit, run from the package directory:

   ```bash
   pgrep -fl "$PWD"
   lsof -nP -iTCP:3000 -iTCP:8000 -iTCP:8001 -sTCP:LISTEN
   ```

   Both commands must print nothing. Reopen VISTA immediately after each check
   to catch delayed cleanup or stale single-instance state.

**Stopping and cleanup** (a built package, ideally with a chat started so a sandbox
exists). Start `./vista`, then stop it each of these three ways:

1. Close the window, or press Cmd-Q (Ctrl-Q on Linux).
2. Press Ctrl-C in the terminal.
3. Close the terminal window.

After each one, run this from the package directory to check that nothing is left:

```bash
pgrep -fl "$PWD" ; lsof -nP -iTCP:3000 -iTCP:8000 -iTCP:8001 -sTCP:LISTEN
```

Both commands should print nothing.

**Walk-through in the window** (`openspec/specs/desktop-window`):

- The Globus "Open in browser" link opens the system browser, and pasting the code
  back completes the connection.
- A DOI link and the settings token links open the system browser.
- "Open PDF" opens a second window.
- Dataset and agent-file downloads show a save dialog.
- Cmd-V into a settings field pastes.
- Dropping a file outside an upload area leaves the page alone.
- A second `./vista` or `npm start` exits and brings the first window forward.
- Over SSH, `./vista` says it cannot open the window and why, and starts nothing.

**On Linux** (`openspec/changes/linux-desktop-window`): a real Ubuntu 24.04 desktop,
and one of Debian 13 or Fedora. A VM is fine, but it needs nested virtualisation:
the launcher refuses a host without KVM. (Its one bypass, the build-only
`VISTA_VERIFY_WITHOUT_SANDBOX=1`, skips the very checks this needs.) Run the stopping
and walk-through checks above, then:

- On stock Ubuntu the window opens without the sandbox, and the launcher prints why along
  with the two `sudo` commands. After running them, the next start prints nothing, and
  `logs/window.log` shows `renderer sandbox: on`.
- On Debian or Fedora it opens sandboxed with no step.
- With the sandbox off, "Open PDF" opens the system browser, not a second window.
- The window opens in both a Wayland session and an X11 session.
- Over SSH, and as root, `./vista` gives its reason and starts nothing.
- With one of the README's window libraries removed, the launcher names what is missing.
- `kill -SEGV` on the window process makes the launcher say the window stopped
  unexpectedly and name its log, and every service stops.
- On Ubuntu, `./launch.sh logs` prints the same sandbox message, and the
  profile turns the sandbox on for it as well.

## Weekly / manual real HPC (`hpc_jobs/example`)

```bash
# MCP must NOT have VISTA_MCP_HPC_DRY_RUN set
export VISTA_RUN_HPC=1
export VISTA_HPC_SMOKE_CLUSTER=odo          # or frontier
export VISTA_HPC_SMOKE_PROJECT=molten-salt   # a science project: seed with VISTA_BACKEND_SEED_SCIENCE_PROJECTS=true
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
