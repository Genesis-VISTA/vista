---
name: splash-planner
description: >-
  Run an autonomous, human-in-the-loop SPLASH campaign: optimize a fusion-reactor molten-salt
  blanket composition to maximize the Tritium Breeding Ratio (TBR) while keeping the salt
  thermophysically viable. Gathers inputs from the user, drafts a plan for approval, then drives
  the campaign tools to dispatch neutronics + chemistry HPC jobs per candidate, scores results,
  and iterates until the user confirms exit. Use when the user asks to run, set up, or explore a
  tritium-breeding / molten-salt-blanket optimization campaign.
metadata:
  version: "0.1.0"
  tags: ["Fusion", "Tritium Breeding", "Molten Salt", "Neutronics", "SPLASH", "Campaign"]
license: Proprietary
---

# SPLASH tritium-breeding campaign (planner)

Drive a multi-cycle campaign that finds a molten-salt blanket composition maximizing the
**Tritium Breeding Ratio (TBR)** subject to thermophysical-viability constraints. You are the
planner: you talk to the user, and you delegate simulation work to the `neutronics` and
`chemistry` subagents via the **campaign tools**. The full scientific rationale is in the
`splash-playbook` (standalone) skill; this skill is the operational version wired to VISTA.

> **v1 scope.** Simulations run as fast analytic-surrogate stub jobs and report a focused metric
> set: **TBR** (neutronics) and **melting point, density, viscosity, thermal conductivity**
> (chemistry). Boiling point, heat capacity, ionic diffusion, corrosion, and tritium
> extractability are **not modeled in v1** — say so in your final report.

## Required user inputs — gather these FIRST

Before launching anything, collect (a structured intake form is fine):

1. **Salt system** — e.g. `FLiBe` (LiF–BeF₂), the baseline.
2. **Design-variable ranges** (defaults if unspecified): Li-6 enrichment `0.075–0.90`,
   temperature `700–1000 K`, Be concentration `0.0–0.01`, blanket thickness `20–80 cm`.
   (TBR > 1.1 generally needs Li-6 ≳ 0.50 — bias the search upward.)
3. **TBR target** — default `1.1`; larger is better.
4. **Target HPC platform** — `odo`, `frontier`, or `perlmutter` (the user must have credentials
   configured for it).
5. **Budget** — candidates per cycle and max cycles.

## Workflow

Drive these campaign tools in order; **never dispatch HPC work without an approved plan**, and
**confirm with the user before each new cycle and before exit**. User edits always take precedence.

1. **`start_campaign(planner_skill="splash-planner", domain="splash", title=…)`** — begins the run;
   returns a `run_id` you pass to every later call.
2. **Gather inputs** (above), then **`set_campaign_spec(run_id, spec=…)`** — persist the agreed
   spec (salt, variable ranges, `tbr_target`, platform, budget).
3. **Draft a numbered plan**, show it to the user, and on approval
   **`save_campaign_plan(run_id, plan=…)`**.
4. **`dispatch_cycle(run_id, candidates=[…], cycle=N, cluster=…)`** — for each candidate (a dict of
   the four variables), this launches neutronics **and** chemistry jobs in parallel and returns
   immediately. Tell the user the jobs are queued and that they'll be **emailed as each completes**;
   results are filled onto the campaign steps by the monitor.
5. When the cycle's jobs have completed, **`get_campaign_status(run_id)`** to read the per-candidate
   results, then **score** them: write the candidates + their metrics to a JSON file and run

   ```bash
   python3 /mnt/skills/splash-planner/scripts/score_candidates.py results.json
   ```

   The scorer filters out candidates that fail the viability constraints, ranks the rest by TBR,
   and reports the best feasible candidate and whether the TBR target is met. Summarize this for
   the user (best-so-far, trends, any infeasible/failed candidates).
6. **Decide:** if the TBR target is met and constraints are satisfied and the user confirms →
   **`finish_campaign(run_id, "converged")`**. Otherwise propose the next cycle with refined ranges
   around the best feasible region, get approval, and go to step 4. If the user wants to stop →
   **`finish_campaign(run_id, "exited")`**.

## Scoring — focused viability set (v1)

`scripts/score_candidates.py` applies (matching `campaign.yaml`):

- **Primary:** maximize **TBR** (target `> 1.1`).
- **Viability filter (drop the candidate if any fail):** melting point `< 550 °C`,
  density `1.8–2.5 g/cm³`, viscosity `< 15 mPa·s`, thermal conductivity `> 0.8 W/(m·K)`.

Input JSON shape (one entry per candidate):

```json
{"tbr_target": 1.1,
 "candidates": [
   {"params": {"li6_enrichment": 0.7, "be_concentration": 0.005, "blanket_thickness": 60, "temperature": 900},
    "metrics": {"TBR": 1.18, "melting_point_c": 480, "density_g_cm3": 2.0,
                "viscosity_mpa_s": 8.0, "thermal_conductivity_w_mk": 1.0}}
 ]}
```

## Guardrails

- No HPC jobs without an approved plan; confirm before each new cycle and before exit.
- The user's instructions and plan edits always override your defaults.
- Cite job ids / results for every number you report, and flag the **v1 unmodeled** properties
  (boiling point, Cp, ionic diffusion, corrosion, tritium extractability) in the final report.
