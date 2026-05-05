---
name: alloy-design
description: Agentic optimization of refractory high-entropy alloy composition on HPC. Start with MoNbTaW (4-element). Propose a composition, submit it to the Andes Slurm cluster via the agenthpc_* MCP tools, wait for the job, read the score, and iterate until the user's targeted transition temperature or the trial budget is reached.
metadata:
  version: "0.2.0"
  tab: alloy-design
  tags: ["OLCF", "Frontier", "Materials Design", "High Entropy Alloy Design"]
license: Proprietary
---

# High Entropy Alloy Design (MoNbTaW) Skill

## Goal

Find a composition of the Mo-Nb-Ta-W refractory high entropy alloy that meets
the user's **targeted critical transition temperature** (Tc). The optimization
runs on the Andes HPC cluster: each candidate composition is submitted as a
Slurm job; when it finishes, the server reads `stat0.dat` and returns the peak
specific-heat temperature as the score.

## Required user inputs — gather these FIRST

Before running any tool, ask the user for:

1. **`target_score`** — the targeted critical transition temperature in K
   (the stopping criterion; e.g. `1250`). If the user says "use the default"
   or does not specify, omit it and the YAML default will be used.
2. **`max_trials`** — the maximum number of HPC jobs you may submit in this
   optimization (e.g. `50` or `100`). Same fallback rule as above.
3. (Optional) any composition constraints the user wants — e.g. "keep Mo ≥ 0.2"
   — and any notes about prior knowledge.

Use a single, short clarifying question to collect these. Once you have them,
pass `target_score` and `max_trials` to every tool call that supports them so
the server's `should_stop`, `threshold_reached`, and `budget_exhausted` flags
reflect the user's actual goal rather than the YAML defaults.

## Tools

All tools are under the `agenthpc_*` prefix. Pass `app_type="monbtaw"` to
every call. Tools that accept `target_score` / `max_trials` overrides are
marked below.

1. **`agenthpc_get_search_space(app_type, target_score?, max_trials?)`** —
   pure metadata (no HPC call). Returns allowed parameter values, the
   sum=1.0 constraint, the effective `score_threshold`, and the effective
   `max_trials`. Call once at the start *after* you have the user's inputs.

2. **`agenthpc_get_all_results(app_type, target_score?, max_trials?)`** —
   returns every trial completed so far plus `best_score`, `best_parameters`,
   `threshold_reached`, `budget_exhausted`, and a combined `should_stop`.
   Call at the start of **every** iteration to decide whether to continue.

3. **`agenthpc_submit_parameter_set(app_type, parameters, trial_number?)`** —
   submits one composition. For MoNbTaW `parameters = [Mo, Nb, Ta, W]` and
   each element must be one of the allowed values (sum=1.0 within 1e-3).
   Returns `{job_id, log_params, parameters}`; also returns `{"cached": true,
   "score": ...}` when the composition has already been scored this session.
   **The user will be asked to confirm every submission.**

4. **`agenthpc_wait_for_job(app_type, job_id, log_params, timeout_s?,
   poll_interval_s?)`** — blocks on the server until the job leaves the
   queue (or the timeout elapses). Prefer this over polling
   `agenthpc_check_job_status` yourself.

5. **`agenthpc_get_job_result(app_type, job_id, log_params, parameters,
   target_score?)`** — reads the log, computes the score, stores it in the
   per-session cache, and returns `{score, metrics, threshold_reached}`.

6. **`agenthpc_list_pending_jobs(app_type)`** — lists the Slurm job ids this
   session has submitted that have not yet returned a score. Use this
   before offering the user a cancel.

7. **`agenthpc_cancel_job(app_type, job_id)`** — cancels one in-flight job
   (runs `scancel` on the HPC side and drops the job from the pending
   registry). Idempotent.

8. **`agenthpc_cancel_all_pending(app_type)`** — cancels every in-flight
   job this session has launched. Completed trials stay in the results
   cache. Use this when the user wants to stop the entire optimization.

9. **`agenthpc_plot_progress(app_type)`** — renders specific-heat vs
   temperature curves for every completed trial so far on a single
   figure (labelled by trial number), saves the PNG, and returns
   `Plot saved to <path>` so the UI auto-displays it in the output panel.
   Call this once after EVERY successful `agenthpc_get_job_result`.

## The optimization loop

```
(0) Ask the user (one short question) for target_score and max_trials.

(1) Call agenthpc_get_search_space("monbtaw", target_score=T, max_trials=N).
    Remember the allowed values and the sum=1.0 constraint.

(2) Loop:
  (a) Call agenthpc_get_all_results("monbtaw", target_score=T, max_trials=N).
      If should_stop is true, STOP — report best_parameters, best_score,
      number of trials run, and whether the target was met.
  (b) Pick a new composition [Mo, Nb, Ta, W] that:
        - represents atom-fraction percentages,
        - sums to EXACTLY 1.0 (the server rejects anything off by > 1e-3 —
          treat 1.0 as an inviolable constraint, not a target),
        - does NOT match any key in the trials list.
      Before calling the tool, verify Mo + Nb + Ta + W == 1.0 by adding the
      four numbers in your head. If they don't sum to 1, rescale them
      (e.g. divide each by the current sum and round to two decimals,
      then nudge one coordinate to absorb the rounding error) BEFORE
      submitting.
      Use the scores in `trials` to guide the choice — explore early,
      exploit near the best-scoring region later.
  (c) Call agenthpc_submit_parameter_set("monbtaw", [Mo, Nb, Ta, W]).
      If cached=true, skip to (a). Otherwise remember job_id and log_params.
  (d) Call agenthpc_wait_for_job("monbtaw", job_id, log_params).
        - status=completed → (e)
        - status=no_log   → report the failure; pick a different
                            composition and continue from (a).
        - timed_out       → wait again or report the stall.
  (e) Call agenthpc_get_job_result("monbtaw", job_id, log_params,
        parameters=[Mo, Nb, Ta, W], target_score=T).
  (f) Call agenthpc_plot_progress("monbtaw"). This refreshes the cumulative
        specific-heat curves figure shown in the output panel so the user
        can watch the campaign evolve visually.
  (g) Emit a **Trial Report** to the chat before moving on. See the
        "Trial Report format" section below — this is mandatory after every
        completed trial, never silent.
  (h) If threshold_reached is true, STOP.

(3) Final report: best composition, its score vs the user's target, number
    of trials run, and a short summary of the search trajectory.
```

## Trial Report format

After every successful `agenthpc_get_job_result` (and after the
`agenthpc_plot_progress` refresh), write a structured report in chat with
EXACTLY these sections, in this order:

```
### Trial N complete
- **Ran:** (Mo, Nb, Ta, W) → Tc = X.X K  (Cv = Y.YY)
- **Why this point:** one sentence — what hypothesis were you testing?
  (e.g. "equiatomic baseline", "perturbed W -0.05 from best-so-far to
  probe the W-lean boundary", "jumped to a Ta-rich corner to widen
  sampling before exploiting").

**Trajectory (n trials)**
| # | Mo | Nb | Ta | W | Tc  |
|---|----|----|----|----|-----|
| 1 | …  | …  | …  | …  | …   |
| … | …  | …  | …  | …  | …   |
(When n > 10, show the first 3, the best-scoring, and the last 5 — mark
the best row with a bullet.)

**Best so far:** (Mo, Nb, Ta, W) → Tc = Z.Z K  (vs target T)

**Next proposed:** (Mo', Nb', Ta', W')
- **Reason:** 1–2 sentences — is this exploration or exploitation? What
  gradient are you following? Verify Mo' + Nb' + Ta' + W' == 1.0 in this
  block and show the sum.
```

Keep each Trial Report under ~15 lines; the plot in the output panel
carries the detailed Cv(T) shape so prose can stay tight. When the loop
ends (threshold_reached or budget_exhausted), write ONE final
**Campaign Summary** instead of another Trial Report.

## Cancellation

The user may ask to stop, abort, or cancel the optimization at any time
("stop", "cancel", "abort", "kill the jobs", "we're done", etc.). When that
happens:

1. Call `agenthpc_list_pending_jobs("monbtaw")` and tell the user how many
   jobs are in flight and which compositions they represent.
2. Call `agenthpc_cancel_all_pending("monbtaw")`. This runs `scancel` for
   each job and clears the pending registry.
3. Then call `agenthpc_get_all_results("monbtaw", target_score=T,
   max_trials=N)` one final time and report the best composition / score
   found among the completed trials, plus how many trials completed vs.
   how many were cancelled.
4. DO NOT submit any new jobs after a cancel request. End the loop.

If the user only wants to cancel a specific job they named (e.g. "cancel
job 12345"), use `agenthpc_cancel_job("monbtaw", "12345")` and continue
the loop.

## Rules

- **Every proposed composition MUST have Mo + Nb + Ta + W == 1.0 (within
  1e-3).** These are atom-fraction percentages; anything else is
  physically meaningless. Verify the sum before calling
  `agenthpc_submit_parameter_set` — the server rejects bad sums, but a
  bounced submission still costs a round-trip.
- **Never** call `submit_hpc_job` or `run_bash` for MoNbTaW — the
  `agenthpc_*` tools are the only correct path. They handle SSH, the run
  directory, the composition file, and the score extraction.
- **Always** pass the user's `target_score` / `max_trials` to every tool
  that accepts them, on every iteration. The server is stateless — it
  won't remember what the user said last turn.
- **Never** skip `agenthpc_get_all_results` at the start of an iteration —
  duplicates waste HPC allocation and can loop forever.
- After each completed trial, include a one-line progress update so the
  user can watch. Keep it terse: `Trial 7: (0.30, 0.25, 0.25, 0.20) → 1187 (best: 1212)`.
- If the user asks you to run a single trial (not an optimization), skip
  the loop and report the single result.
- The first submission in a session elicits the user's Andes credentials;
  subsequent submissions reuse the cached SSH connection.
