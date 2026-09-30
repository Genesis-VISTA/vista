---
name: alloy-tc-planner
description: >-
  Run an autonomous, human-in-the-loop alloy-design campaign: search MoNbTaW refractory
  high-entropy alloy compositions for the highest order-disorder transition temperature
  (Tc), subject to composition and ordering constraints. Gathers inputs from the user,
  drafts a plan for approval, then drives the campaign tools to dispatch one parallel-
  tempering Monte Carlo HPC job per candidate composition, scores the results, and
  iterates until the user confirms exit. Use when the user asks to run, set up, or
  explore an alloy-design / high-entropy-alloy optimization campaign, to search or
  screen compositions for a target transition temperature, or to maximize Tc.
metadata:
  version: "0.1.0"
  tags: ["Materials Design", "High Entropy Alloy Design", "Monte Carlo", "HPC", "Campaign"]
license: Proprietary
---

# Alloy Tc campaign (planner)

Drive a multi-cycle campaign that finds the MoNbTaW composition with the highest
**order-disorder transition temperature (Tc)**, subject to composition and ordering
constraints. You are the planner: you talk to the user, and you delegate simulation work
to the `thermo` subagent (`alloy-thermo-mc`) via the **campaign tools**.

> **v1 scope.** The subagent is the real simulation: `alloy-thermo-mc` runs a
> parallel-tempering lattice Monte Carlo on DFT-derived pair interactions and returns
> **Tc** plus short-range-order and run-quality signals. Only **MoNbTaW on BCC** has
> fitted couplings — any other alloy system is blocked on its DFT parameters, and you
> must say so rather than substituting another system's couplings.

## Required user inputs — gather these FIRST

Before launching anything, collect:

1. **Target Tc** (K) — the stopping threshold, e.g. `1250`. Larger is better; the
   campaign maximizes Tc and treats the target as "good enough, propose exit".
2. **Composition constraints** (optional) — per-element floors/ceilings, e.g.
   "keep Mo ≥ 0.2". Passed to the scorer as `bounds`.
3. **Target HPC platform** — `odo` (default) or `frontier`. The user must have
   credentials configured for it.
4. **Budget** — candidates per cycle and max cycles. Each candidate is one HPC job of
   roughly 5 minutes on 2 nodes.

Ask in one short round, not one question at a time.

## The hard constraint: compositions live on the simplex

**Mo + Nb + Ta + W must equal 1.0 (± 1e-3), and every fraction must be ≥ 0.** These are
atom fractions; anything else is physically meaningless.

The `campaign.yaml` declares each variable's range as `[0, 1]` because the manifest
schema has no way to express a constraint *coupling* variables. **Do not treat the four
ranges as independent.** Before every `dispatch_cycle`, add the four numbers and confirm
the sum. If a draft sums to e.g. 0.95, rescale (divide each by the sum, round to two
decimals, then nudge one coordinate to absorb the rounding error) *before* dispatching.
The job wrapper rejects bad sums and the scorer re-checks, but a bounced submission
still costs a round-trip.

## Workflow

Drive these campaign tools in order; **never dispatch HPC work without an approved
plan**, and **confirm with the user before each new cycle and before exit**. User edits
always take precedence.

1. **`start_campaign(planner_skill="alloy-tc-planner", domain="alloy-tc", title=…)`** —
   begins the run; returns a `run_id` you pass to every later call.
2. **Gather inputs** (above), then **`set_campaign_spec(run_id, spec=…)`** — persist the
   agreed spec (target Tc, bounds, platform, budget).
3. **Draft a numbered plan**, show it to the user, and on approval
   **`save_campaign_plan(run_id, plan=…)`**.
4. **`dispatch_cycle(run_id, candidates=[…], cycle=N, cluster=…)`** — each candidate is a
   dict `{"mo":…, "nb":…, "ta":…, "w":…}`. This launches one Monte Carlo job per
   candidate and returns immediately. Tell the user the jobs are queued and that they'll
   be **emailed as each completes**; results are filled onto the campaign steps by the
   monitor.
5. When the cycle's jobs have completed, **`get_campaign_status(run_id)`** to read the
   per-candidate results, then **score** them: write the candidates + their metrics to a
   JSON file and run

   ```bash
   python3 /mnt/skills/alloy-tc-planner/scripts/score_candidates.py results.json
   ```

   Summarize for the user (best-so-far, trends, rejected candidates and why).
6. **Decide:** target met, constraints satisfied, and the user confirms →
   **`finish_campaign(run_id, "converged")`**. Otherwise propose the next cycle with
   compositions refined around the best feasible region, get approval, and go to step 4.
   If the user wants to stop → **`finish_campaign(run_id, "exited")`**.

## Scoring — Tc-primary, composition- and ordering-gated

`scripts/score_candidates.py` applies (matching `campaign.yaml`):

- **Primary (ranked):** maximize **`Tc_cv_K`** (the specific-heat-peak estimate). The
  target is a stopping threshold, *not* a ranking term.
- **Hard gate — composition:** sum = 1.0 ± 1e-3, all fractions ≥ 0, plus any user bounds.
- **Hard gate — ordering:** `|sro_alpha1| ≥ 0.05`. A specific-heat bump with no
  short-range order is not an order-disorder transition. (The floor is a screening
  heuristic, not a derived threshold — say so if it decides an outcome.)
- **Hard gate — bracketed peak:** `peak_bracketed` must be true. This one is *not*
  merely a quality signal: an unbracketed peak is reported at the ladder's high endpoint,
  so it looks like the **highest-Tc candidate in the cycle** and would win a maximize-Tc
  ranking outright. Rejecting it keeps broken runs from steering the campaign. When a
  candidate is rejected this way, propose it again with a widened `--t-init`/`--t-final`
  rather than discarding the composition.
- **Advisory (reported, never gates or ranks):** `estimators_agree` (Cv- and chi-peak Tc
  within 15%), `swap_accept_mean` (healthy ≈ 0.2–0.4; lower means the replica ladder is
  too coarse to equilibrate).

Input JSON shape:

```json
{"tc_target": 1250,
 "bounds": {"mo": [0.2, 1.0]},
 "candidates": [
   {"params": {"mo": 0.30, "nb": 0.25, "ta": 0.25, "w": 0.20},
    "metrics": {"Tc_cv_K": 1180.0, "Tc_chi_K": 1240.0, "sro_alpha1": -0.31,
                "swap_accept_mean": 0.28, "peak_bracketed": true,
                "estimators_agree": true}}]}
```

## Search guidance — this is your judgment, not a setting

There is **no optimizer in the backend.** `campaign.yaml` carries a `search.strategy`
key, but nothing reads it. Choosing the next compositions is entirely your job, using the
results so far. Work as follows:

- **Cycle 1 — spread out.** Include the equiatomic point `(0.25, 0.25, 0.25, 0.25)` as a
  baseline, plus a few corner-biased compositions (one element at 0.40–0.55, the rest
  sharing the remainder). Cover the space before exploiting it.
- **Later cycles — exploit around the best.** Perturb one or two elements at a time from
  the best feasible candidate, ±0.05, renormalizing to keep the sum at 1.0. Follow the
  gradient the trajectory implies.
- **Never resubmit a composition already evaluated** — check the campaign status first.
  Duplicates waste allocation and can loop forever.
- **Quantize to two decimals.** It keeps the sum arithmetic exact and avoids
  near-duplicate candidates that cost a job but add no information.
- **Respect user bounds** in every proposal; they are part of the approved spec.

## Trial Report format

After each scored cycle, write a structured report in chat:

```
### Cycle N complete
- **Ran:** M candidates → best (Mo, Nb, Ta, W) = … → Tc = X.X K
- **Why these points:** one sentence — what did this cycle test?

**Trajectory (n candidates so far)**
| # | Mo | Nb | Ta | W | Tc (K) | gate |
|---|----|----|----|----|--------|------|
(When n > 10, show the first 3, the best, and the last 5 — mark the best row.)

**Best so far:** (Mo, Nb, Ta, W) → Tc = Z.Z K  (vs target T)
**Rejected:** composition → reason (SRO / bracketing / bounds)

**Next proposed:** list each candidate with its sum verified = 1.00
- **Reason:** 1–2 sentences — exploration or exploitation? Which gradient?
```

Keep it tight; the per-job `thermo.png` / `order.png` carry the detailed curves. When the
campaign ends, write ONE **Campaign Summary** instead of another Trial Report.

## Guardrails

- No HPC jobs without an approved plan; confirm before each new cycle and before exit.
- The user's instructions and plan edits always override your defaults.
- **Verify the simplex sum for every candidate before dispatching.**
- Cite job ids / results for every number you report.
- Flag in the final report: the SRO floor is a heuristic; a single lattice size cannot
  pin Tc precisely (finite-size scaling would be needed); and only MoNbTaW has fitted
  DFT couplings.
