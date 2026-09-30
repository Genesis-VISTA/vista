# Multi-Agent Campaign Framework

A reusable **planner + subagents** orchestration layer for VISTA. The goal is to build the
*common usage pattern* once, domain-agnostically, and then add each scientific domain as a
thin set of **skills** (VISTA's existing skill mechanism), with no backend changes. SPLASH
(tritium-breeding molten-salt optimization) is the first instantiation; the alloy/HPC
worker-pool case on `origin/subagent` is a second one that drops onto the same framework
purely as skills.

> Status: design / planning. Target branch: `splash-planner`. See
> [`splash-planner-playbook.md`](./splash-planner-playbook.md) for the SPLASH instantiation.

## Why a framework first

The naive path is to hard-code a tritium planner with two simulation agents. But the same
shape — *an orchestrator that gathers a goal from the user, drafts a plan, delegates work to
specialist subagents that launch and monitor long-running HPC jobs, evaluates results against
a goal metric, and loops with the user in the loop until they confirm exit* — recurs across
domains. Building the orchestration, durable state, HPC-job tracking, notification, and
resumption once means a new domain is added as **skills** — a planner skill (playbook +
manifest + scoring) and one simulation skill per subagent role — with no backend changes.

## Core concepts

- **Planner (orchestrator) runtime** — generic agent code, specialized by a **playbook skill**.
  Runs a domain-agnostic state machine (intake → plan → delegate → monitor → evaluate → iterate →
  exit), owns the human-in-the-loop (HITL) conversation, and never launches work without an
  approved plan. User edits to the plan always take precedence. The *what* (workflow, goal
  metric, variables, scoring) comes entirely from the playbook skill.
- **Subagent (specialist/worker) runtime** — generic agent code, specialized by a **simulation
  skill**. The sim skill tells it how to turn an *order* into a job spec, which `hpc_jobs/<name>`
  to submit, and how to parse outputs into a structured *result*. Two operations: **dispatch**
  (build spec → `submit_hpc_job` → record `HpcJob` → return job refs) and **collect** (parse
  outputs → structured result). N instances per role, bounded by budget. Subagents don't talk to
  each other; the planner and durable state coordinate them.
- **Campaign composition (skill-native)** — a campaign *is* a set of skills, declared by a
  `campaign.yaml` manifest inside the planner skill (schema below). The backend reads the
  manifest to know which subagent roles exist, which sim skill + HPC job each binds to, the
  design variables, and the metric targets. **Scoring/ranking/convergence lives in the planner
  skill** (deterministic scripts run in the sandbox via `run_bash`, guided by the planner) — not
  in backend code. New domains = new skills, zero backend changes.
- **Durable campaign state** — a campaign run, its plan/step graph, and a persistent HPC job
  registry, all in the DB so the workflow survives a backend restart.
- **Monitor + notifier** — a background loop that polls in-flight jobs, emails the user on
  completion, writes results back to the step, and nudges the planner.

## Campaign composition (skill-native)

A planner skill carries a `campaign.yaml` alongside its `SKILL.md`. This is the *only* place a
campaign is defined; the backend stays domain-agnostic. Draft schema:

```yaml
# campaign.yaml — inside the planner skill directory
domain: splash
variables:                                   # design variables the planner elicits / optimizes
  - {name: li6_enrichment,   range: [0.075, 0.90], unit: fraction}
  - {name: temperature,      range: [700, 1000],   unit: K}
  - {name: be_concentration, range: [0.0, 0.01],   unit: fraction}
  - {name: blanket_thickness, range: [20, 80],     unit: cm}
metrics:
  primary: {name: TBR, target: 1.1, direction: maximize}
  scorer: scripts/score_candidates.py         # deterministic scorer, run in the sandbox
subagents:                                    # role → sim skill → hpc job; count is a default, not a cap
  - role: neutronics
    skill: neutronics-shift
    job: neutronics
    default_count: 1
    collect_files: [results.json]             # outputs the result parser needs
    args:                                     # how a candidate becomes script_args
      encoding: flags                         # flags (flat CLI, what real jobs parse) | json
      map: {li6_enrichment: --li6}            # candidate variable → CLI flag, per role
      extra: "--allow-extrapolation"          # fixed, non-candidate arguments
  - role: chemistry
    skill: chemistry-supersalt
    job: chemistry
    collect_files: [results.json]
    args:
      map: {temperature: --temperature}
search:
  strategy: bayesian                          # bayesian | grid | llm
```

**`args` and `collect_files` are optional and per-role.** The mapping is per-role because
one candidate feeds roles that need different subsets under different flag names. A role
that declares neither behaves exactly as it did before these fields existed: the whole
candidate is serialized as JSON into `script_args`, and no output files are fetched at
collect time. Adoption is therefore per-campaign and opt-in.

## Mapping onto existing VISTA

| Need | Reuse from | Notes |
|---|---|---|
| Turn loop + streaming + HITL elicitation | `main` (`ProjectAgent.run_stream`, MCP elicitation, tool-approval) | Planner is a campaign-mode chat agent reusing this turn/elicitation infra. |
| Delegation (agent-as-tool) + progress callbacks | `origin/subagent` pattern | Subagents exposed to the planner as `@agent.tool`; worker progress streamed back through the existing `StreamMerger` callbacks. Re-implemented on current `main`, not merged. |
| Durable conversation/session history | **`main`** (`services/chat_session.py`, `api/chat_sessions.py`) | Merged from the `session` branch; session-scoped agent state + `message_history` persistence. Build the campaign resume substrate directly on it. |
| HPC submit/monitor/outputs | `main` (`submit_job_mcp.py` tools) | Add a **persistent** job registry — today `_submitted_jobs` is in-memory and is lost on restart. |
| Email | new | `services/email.py` + SMTP config; driven by the monitor, not the compute node (no outbound egress on OLCF/NERSC compute). |

## Generic playbook contract (domain-agnostic skeleton)

Every planner skill fills these phases in. SPLASH's filled-in version lives in the
[SPLASH playbook](./splash-planner-playbook.md).

- **A · Intake** — elicit & confirm the goal, design variables + ranges, goal metric + targets,
  constraints, budget/cycle ceilings, target HPC platform + credentials, search strategy,
  notification email, check-in cadence, and the definition of "done."
- **B · Plan** — restate the spec; propose a numbered, cyclic plan; show estimated jobs /
  platform / walltime; **ask to approve or edit** (user edits override defaults); persist the
  agreed plan as the source of truth.
- **C · Execute a cycle** — for each candidate, issue orders to the relevant subagents; they
  submit jobs, register them durably, and report job IDs; set step `running`; tell the user what
  is queued and that they will be emailed on completion; pause (resumable).
- **D · Gather & evaluate** — on completion, collect outputs; run the playbook skill's scorer
  (deterministic skill script via `run_bash`, guided by the planner) to apply constraints, rank,
  and test convergence; summarize for the user; decide exit / next-cycle / remediation. Surface
  the decision; user suggestions take precedence.
- **E · Iterate** — apply user edits → update the persisted plan → loop to C.
- **F · Resume** — on restart / session reopen, rehydrate plan + step statuses + in-flight
  job IDs, reconcile job states, resume monitoring, summarize "where we are" before continuing.
- **G · Exit (only on explicit user confirmation)** — final report: recommendation, metric +
  property values, provenance (job IDs/outputs), cycles run, resources used; mark
  `converged`/`exited`; stop monitors.

**Cross-cutting guardrails (enforced by the framework):** no HPC jobs without an approved plan
+ launch confirmation; user edits always win; confirm before each new cycle and before exit;
checkpoint after every step; validate platform + credentials before submitting; honor
budget/cycle ceilings; cite job IDs/outputs for every reported number.

## Durable data model (generic)

- `CampaignRun` — id, project/user/session linkage, domain key, spec (variables, ranges, metric
  targets, constraints, platform, budget) as JSON, plan (ordered steps) as JSON, status
  (`gathering → planning → running → awaiting_user → converged → exited`), timestamps.
- `CampaignStep` — id, run id, cycle index, kind (per-subagent or `decision`), order spec (JSON),
  status, result (JSON), linked job id(s).
- `HpcJob` — job_id (PK), user/project, campaign-step link, cluster, log/output paths, state,
  submitted_at, last_polled_at. Durable replacement/backstop for the in-memory
  `_submitted_jobs` cache so a previously-submitted job stays pollable after a restart.

## PR plan (stacked)

**PR 1 — Generic framework (domain-agnostic, reviewable on its own).** ✅ Landed. Built as a
sequence of commits, each leaving the tree green:
1. **Design + docs** — this doc + the SPLASH playbook under version control.
2. **Campaign data model** — `CampaignRun` / `CampaignStep` / `HpcJob` tables + status enum + CRUD tests.
3. **Restart-safe HPC job registry** — persist `submit_job_mcp.py`'s `_submitted_jobs`, rehydrate on startup.
4. **Campaign service** — `services/campaign.py`: create/load/resume run, persist plan, steps, transitions.
5. **Generic subagent runtime** — skill-specialized worker (dispatch/collect) on the HPC tools.
6. **Generic planner runtime + delegation + manifest loader** — `manifest.py` (`campaign.yaml`
   loader), `hpc_tools.py` (`McpHpcTools` over an injected MCP invoke), `planner.py`
   (`CampaignPlanner` delegation engine + `build_subagents` + playbook prompt builder).
7. **Monitor + email + resume** — `services/email.py` + SMTP config; `CampaignMonitor`
   (poll → collect/fail → notify) + terminal-state classification + resume query.
8. **Campaign API + wiring + mock-domain end-to-end test** — `api/campaign.py` (CRUD + state),
   `wiring.py` (the monitor↔MCP/planner seams: `parse_job_state` / `build_status_poll` /
   `build_collector`), and an end-to-end test driving create → dispatch → poll → collect →
   email → resume → exit over a mock domain with the HPC boundary faked.

**PR 2 — Live framework wiring (domain-agnostic; depends on PR 1).** Turns PR 1's programmatic
engine into a campaign that actually *runs* — conversationally and monitored. Still no domain
code; verified against the mock domain (no real MCP / LLM / HPC needed in tests):
1. **Design/docs** — this split + the parked PR 3 decisions.
2. **Live MCP invoke** — `mcp_invoke.py`: `unwrap_tool_result` + `build_invoke(call_tool, user,
   project_paths)` (builds the `{"vista":{"user","project_paths"}}` metadata) + `project_paths_for`
   + a thin live `build_mcp_invoke` over `get_vista_mcp_server()`. Pure parts unit-tested.
3. **Per-job wiring + monitor session** — `build_invoke_for_job` / `build_planner_for_job` (load
   run → planner skill dir → `load_manifest` → `build_subagents` with real `McpHpcTools`); refactor
   the monitor's `poll` to `poll(session, job)` so it derives per-job user creds.
4. **Conversational planner driver** — thread the request `AsyncSession` + progress emitters
   (`_cur_db_session` / `_cur_progress_emitter`, reusing the `subagent` pattern) through
   `run_stream`; register campaign `@agent.tool`s (`start_campaign`, intake via elicitation forms,
   `propose/edit_plan`, `dispatch_cycle`, `gather_and_score`, `decide`, `finish`) over
   `CampaignPlanner`. HITL = **hybrid** (elicitation forms for intake, chat for approvals/edits).
   Driven deterministically in tests via PydanticAI `FunctionModel`; default chat behavior
   unchanged when no campaign is active.
5. **Lifespan monitor start** — `CampaignSettings` (on/off + interval); start
   `CampaignMonitor.run_forever` in the app lifespan with the real `poll`/`collect`, resuming open
   jobs; stop on shutdown. Gated.
6. **Mock-domain live end-to-end test** — a `FunctionModel` planner drives intake→plan→dispatch
   through the real agent tools; the monitor (fake MCP `invoke`, on-disk mock planner+sim skills)
   polls→collects→emails→resumes. Proves the whole live path with no real MCP/LLM/HPC.

**PR 3 — SPLASH skills + UI (depends on PR 2).**
1. `splash-planner` skill: the playbook (`SKILL.md`), `campaign.yaml` manifest, and the
   **tiered TBR-first scorer** (`scripts/score_candidates.py`) using the thresholds in
   [`splash-planner-playbook.md`](./splash-planner-playbook.md).
2. `neutronics-shift` and `chemistry-supersalt` sim skills + `hpc_jobs/neutronics/` and
   `hpc_jobs/chemistry/` stub jobs. Stubs compute an **analytic surrogate** (cheap correlations so
   the optimization shows realistic trends — TBR rising with Li-6/Be/thickness, properties varying
   with composition), behind a **solver-swappable interface** so real Shift / SuperSalt drop in
   later without changing the skill/agent contract.
3. UI: a **focused read + resume** campaign panel (swappable right-column view showing plan +
   per-step status + job states, polling the state endpoint, with a Resume action); campaign
   creation + plan edits go through the chat planner, not the UI.
4. End-to-end smoke test (Playwright).

**PR 4 (optional follow-on) — Alloy/HPC worker-pool instantiation.** Port the `origin/subagent`
use case onto the framework purely as skills (a coordinator playbook skill + a proposer sim skill
+ its HPC job). Proof that the abstraction generalizes; requires *no* core framework changes.

## Decisions locked

- Native VISTA agents (not wrapping the external `tritium-splash-orchestrator`).
- Stub simulations first; real solvers (Shift / SuperSalt) as a follow-up.
- Full restart-durable resumption (DB session history + durable job registry + plan checkpoints).
- Email via a backend monitor → SMTP (compute nodes have no outbound egress).
- Objective = maximize TBR subject to chemistry-viability constraints; each cycle runs
  neutronics + chemistry in parallel on every candidate.
- **Skill-native composition**: a campaign is defined by a `campaign.yaml` manifest inside the
  planner skill (subagent roles → sim skills → HPC jobs, variables, metric targets). Backend is
  domain-agnostic; new domains add only skills.
- **Scoring lives in the planner skill** (deterministic scripts run in the sandbox), not in
  backend code — editable per domain without redeploying.
- **PR staging**: PR 2 lands the domain-agnostic live wiring (verified on the mock domain); PR 3
  adds the SPLASH skills + UI on top — keeping the framework reusable and reviewable apart from
  any one domain.
- **HITL = hybrid**: elicitation forms for structured intake (salt, ranges, platform, targets,
  budget); conversational chat turns for plan approval, edits, and continue/exit decisions.
- **Stub fidelity = analytic surrogate** behind a solver-swappable interface: realistic
  optimization trends now, with real Shift / SuperSalt as a later drop-in.
- **Campaign UI = focused read + resume**: the panel surfaces plan/step/job state and a resume
  action; campaign creation and plan edits happen through the chat planner.
