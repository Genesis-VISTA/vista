# SPLASH Planner Playbook

The SPLASH instantiation of the [multi-agent campaign framework](./multi-agent-framework.md):
a planner agent that optimizes a **fusion-reactor molten-salt blanket composition to maximize
tritium production**, delegating to a **neutronics** subagent and a **chemistry** subagent.

This document is the planner's workflow (its system-prompt source). It fills in the generic
phase contract (A–G) with SPLASH specifics, and pins the goal metric to the project's agreed
acceptance criteria.

> Domain context: the blanket must breed and let us extract tritium (≥ ~300 lb T per GW-yr),
> shield the magnets, cool the first wall, and transfer heat — *simultaneously*. The salt's
> composition drifts under neutron irradiation (fission products), so candidates are evaluated
> as the **full mix** (initial salt + irradiation products), not just the nominal formula.

## Scope (v1)

- **In scope:** planner + neutronics subagent + chemistry subagent.
- **Subagent count:** the neutronics and chemistry subagents default to **one instance each, but
  are not limited to one** — the planner may fan out multiple instances of either (e.g. one per
  candidate, or to parallelize a large candidate set) within the cycle's job/resource budget.
- **Deferred (framework leaves room):** a **quantum-chemistry / tritium-affinity** subagent that
  scores how extractable the bred tritium is (ideal: tritium bubbles out as gas; bad: tightly
  bound). The real Splash loop feeds affinity back into the orchestrator; v1 omits it and the
  planner should flag tritium extractability as a known, unmodeled risk.

## Design variables (elicited; defaults from the SPLASH orchestrator)

| Variable | Default range | Unit | Notes |
|---|---|---|---|
| Li-6 enrichment | 0.075 – 0.90 | fraction | TBR > 1.1 implies Li-6 ≳ 0.50, so search skews to the upper half. |
| Temperature | 700 – 1000 | K | Evaluated/operating temperature; see operating-window constraint below. |
| Be concentration | 0.0 – 0.01 | fraction | Be is the neutron multiplier. |
| Blanket thickness | 20 – 80 | cm | ≥ ~3 ft (≈ 90 cm) is the industry rule of thumb for shielding; thinner trades shielding for TBR economics. |

**Required structural constraints (hard):** the candidate must contain Li-6 (breeding), a
neutron multiplier (Be, or Pb), and a halide former (F or Cl). Prefer low-Z constituents (Be, F)
for slower radioactive fission-product buildup; prefer low electrical conductivity (the salt
flows in an intense magnetic field). `FLiBe` (LiF–BeF₂) is the industry baseline.

## Goal metric — TBR-first, priority-tiered acceptance

Objective: **maximize the Tritium Breeding Ratio (TBR) subject to chemistry-viability
constraints.** Source of truth for thresholds: `evaluation-metrics.txt`. Properties are largely
unchanged by adding tritium at PPM level **except corrosion**.

| Tier | Property | Target | Direction | Owner |
|---|---|---|---|---|
| 1 — primary | **TBR** | **> 1.1** | larger is better | neutronics (Shift) |
| 2 — phase | Melting point | < 550 °C (823 K) | lower is better | chemistry |
| 2 — phase | Boiling point | > 1000 °C (1273 K) | higher is better | chemistry |
| 2 — phase | Operating window | 500–800 °C; operate ≥ ~100 °C above melting | — | derived |
| 3 — transport | Density | 1.8–2.5 g/cm³ (penalize > 3) | higher better, but > 3 ⇒ pumping-power penalty | chemistry |
| 3 — transport | Heat capacity Cₚ | > 1.5 kJ/(kg·K) | higher is better | chemistry |
| 3 — transport | Thermal conductivity k | > 0.8 W/(m·K) | higher is better | chemistry |
| 3 — transport | Viscosity | < 15 mPa·s | lower is better | chemistry |
| 3 — transport | Ionic diffusion | > ~1×10⁻⁹ m²/s | higher is better | chemistry |
| 4 — corrosion | Redox potential / dissolution free energy | e.g. ΔG(Cr→CrF₂) > 0 ⇒ corrosion thermodynamically unstable (good) | more positive is better | chemistry |

Ranking rule: among candidates that satisfy the Tier-2 phase constraints, maximize TBR; use
Tier-3/4 as tie-breakers and feasibility filters. A candidate that misses TBR > 1.1 is not a
solution even if its chemistry is excellent. Convergence: TBR > target **and** constraints met
(and the user confirms), or marginal TBR gain < ε over a cycle, or max cycles, or user stop.

> Units note: `evaluation-metrics.txt` writes density as "kg/cm³" and Cₚ as "kJ/kg"; these are
> g/cm³ and kJ/(kg·K) respectively.

## Simulation codes

- **Neutronics subagent → Shift** (Monte-Carlo neutron transport; Denovo/SCALE family). Computes
  TBR, radiation-shielding performance, and the **fission-product inventory / concentrations by
  blanket depth** — i.e. the irradiation-evolved composition handed to chemistry.
- **Chemistry subagent → SuperSalt** (equivariant MLIP / MD, near-DFT) for transport &
  thermophysical properties of the *full mix*; melting/boiling from thermochemical models
  (MSTDB-TC) / Gaussian-process surrogates. May propose additives (e.g. to neutralize O/N
  fission products or suppress TF formation for corrosion control).

v1 ships **stub** jobs that emit physically-plausible values for the metric set above from the
input spec; real Shift/SuperSalt integration is a follow-up.

## Skills that compose this campaign

Per the [framework](./multi-agent-framework.md), SPLASH is a set of skills over a domain-agnostic
backend — no backend code is domain-specific:

- **`splash-planner`** (planner skill) — this playbook as `SKILL.md`, a `campaign.yaml` manifest
  (the four design variables, the TBR target, the two subagent roles), and
  `scripts/score_candidates.py` — the deterministic **tiered TBR-first scorer** (drop Tier-2
  infeasible → rank feasible by TBR → Tier-3/4 tie-breakers/flags) the planner runs via `run_bash`.
- **`neutronics-shift`** (sim skill) — how the generic subagent builds a Shift order, submits
  `hpc_jobs/neutronics`, and parses TBR + fission-product inventory + shielding from outputs.
- **`chemistry-supersalt`** (sim skill) — how the generic subagent builds a SuperSalt order,
  submits `hpc_jobs/chemistry`, and parses the full-mix thermophysical properties from outputs.

`campaign.yaml` binds `neutronics → neutronics-shift → hpc_jobs/neutronics` and
`chemistry → chemistry-supersalt → hpc_jobs/chemistry`, each `default_count: 1` (not capped).

## Search & data grounding

- Default search strategy: **Bayesian optimization** over the composition space
  ("MLIP-MD + BO search" on the VISTA roadmap); coarse grid sweep as an alternative.
- Ground intake and candidate selection in VISTA's existing assets: the **MSTDB-TP**
  (thermophysical) and **MSTDB-TC** (thermochemical) databases and the molten-salt **RAG**
  corpus — to seed known compositions and sanity-check predictions.

## Workflow (fills in the generic A–G contract)

**A · Intake — gather & confirm (elicitation)**
- Salt system(s) (default baseline `FLiBe`).
- Design variables + ranges (table above); confirm which are free vs. fixed; note TBR > 1.1
  ⇒ Li-6 ≳ 0.50.
- Goal: maximize TBR (> 1.1) subject to the tiered chemistry constraints; confirm any tightened
  targets.
- Budget & limits: max jobs / node-hours / wall-clock, max cycles, candidates per cycle.
- Target platform (`odo` / `frontier` / `perlmutter`) + nodes/walltime; verify HPC credentials.
- Search strategy (BO default) and any seed compositions from MSTDB/RAG.
- Notification email; check-in cadence; definition of "done."

**B · Draft the plan — present for approval**
- Restate the spec (variables, ranges, TBR target + tiered constraints, platform, budget).
- Propose the cyclic plan:
  1. Pick the cycle's candidate set (BO-proposed or grid points within ranges).
  2. **For each candidate, dispatch neutronics and chemistry jobs in parallel.**
  3. Collect per-candidate TBR (+ uncertainty), fission-product inventory, and thermophysical
     properties of the full mix.
  4. **Filter by Tier-2 phase constraints; among the feasible set, rank by TBR** (Tier-3/4 as
     tie-breakers / feasibility).
  5. Decision gate → exit or refine ranges around the best feasible region.
- Show estimated job count (≈ 2 × candidates), platform, walltime, notification plan.
- **Ask to approve or edit — user edits override defaults.** Persist the plan.

**C · Execute a cycle — delegate to the subagents**
- For each candidate, issue **parallel** orders to the neutronics and chemistry agents
  (composition, platform, resources).
- Subagents submit jobs, register them durably, report job IDs; record jobs → step; set
  `running`; tell the user what is queued and that they'll be **emailed on completion**; pause.

**D · Gather & evaluate**
- On completion, collect TBR + fission products (neutronics) and full-mix properties (chemistry).
- Run the tiered evaluator: drop Tier-2-infeasible candidates, rank feasible ones by TBR,
  surface Tier-3/4 trade-offs and any corrosion flags. Summarize best-so-far, trend, failures,
  resource spend.
- Decide: **TBR > 1.1 and constraints met (and user confirms) → exit**; marginal gain < ε or max
  cycles → propose exit; else propose the next cycle with refined ranges. **User suggestions take
  precedence over the proposed next step.**

**E · Iterate** — apply user edits (ranges, targets, candidates, platform, budget) → update the
persisted plan → loop to C.

**F · Resume** — rehydrate plan / step statuses / in-flight job IDs; reconcile job states; resume
monitoring; summarize "where we are" before continuing.

**G · Exit (only on explicit user confirmation)** — final report: recommended composition(s),
TBR + property values vs. each tier, provenance (job IDs/outputs), cycles run, resources used,
and the **unmodeled tritium-extractability caveat**; mark `converged`/`exited`; stop monitors.

## Guardrails

- Never launch HPC jobs without an approved plan + launch confirmation.
- User instructions/edits always win; confirm before each new cycle and before exit.
- Checkpoint after every step (resumable); validate platform + credentials before submitting;
  respect budget/cycle ceilings.
- State assumptions and uncertainties (including TBR uncertainty and the deferred tritium-affinity
  modeling); cite job IDs/outputs for every reported number.
