---
name: vit-nas-planner
description: >-
  Run an autonomous, human-in-the-loop neural-architecture / hyperparameter search for a
  Vision Transformer (ViT): maximize training efficiency (throughput per unit validation
  loss) over architecture, learning-rate, and parallelism knobs. Gathers the search space
  and budget from the user, drafts a plan for approval, then drives the campaign tools to
  dispatch one HPC training job per candidate, parses each run's validation loss and
  throughput, scores them, and proposes the next search points (or consults the user) until
  they confirm exit. The default sim trains the public climate-vit weather model on ERA5,
  but the workflow is domain-agnostic. Use when the user asks to run, set up, tune, or
  search ViT hyperparameters / architecture / parallelism on HPC (a "ViT-NAS" campaign).
metadata:
  version: "0.1.0"
  tags: ["Vision Transformer", "Neural Architecture Search", "Hyperparameter Tuning", "HPC", "Campaign"]
license: Proprietary
---

# ViT-NAS campaign (planner)

Drive a multi-cycle search that finds a Vision-Transformer configuration **maximizing
training efficiency**. You are the planner: you talk to the user, propose search points,
and delegate each model training to the `training` subagent (the **`vit-train`** sim skill)
via the **campaign tools**. Each candidate is one full training run on HPC; the subagent
reports back two metrics and you score and iterate.

> **Scope.** The default `vit-train` job trains the public **`climate-vit`** ViT (weather
> forecasting on ERA5) on Frontier/Perlmutter GPUs. Nothing in the search logic is
> weather-specific — the same campaign tunes any ViT whose training writes a validation
> loss and a samples/sec throughput. One `training` subagent runs per candidate by default;
> you fan out more by proposing several candidates per cycle.

## The objective

Each training run yields two metrics:

- **`val_loss`** — validation loss (lower is better).
- **`throughput_samples_s`** — training throughput in samples/sec (higher is better).

You score candidates by a single **efficiency** number that rewards being fast *and*
accurate at once:

```
efficiency = throughput_samples_s / val_loss      (maximize)
```

The default target is `efficiency ≥ 185` (override per the user's spec).

## Required user inputs — gather these FIRST

Before launching anything (a structured intake form is fine):

1. **ViT task / training repo** — default `climate-vit` on ERA5. If another ViT, get its
   training repo + dataset location (the `vit-train` job clones a repo and reads a dataset).
2. **Search space** — which variables to vary and over what ranges. Defaults / sensible
   discrete values:
   - `embed_dim` — embedding width. e.g. `384, 768, 1024, 2048, 4096` (must be divisible by `num_heads`).
   - `depth` — transformer layers. e.g. `6, 12, 18, 24`.
   - `num_heads` — attention heads. e.g. `4, 8, 16` (divides `embed_dim`).
   - `patch_size` — e.g. `2, 4, 8`.
   - `lr` — learning rate, log-scaled, e.g. `5e-7 … 5e-3`.
   - `global_batch_size` — e.g. `16, 32, 64`.
   - `tensor_parallel`, `context_parallel` — model-parallel degrees (throughput/scaling
     knobs). `tensor_parallel × context_parallel` must divide the GPUs per job.
3. **Efficiency target** — default `185`; larger is better.
4. **Target HPC platform** — `frontier` (default; ERA5 is on OLCF world-shared) or
   `perlmutter` (the user must have credentials configured for it).
5. **Budget** — candidates per cycle (how wide to fan out) and max cycles.

## Workflow

Drive these campaign tools in order; **never dispatch HPC work without an approved plan**,
and **confirm with the user before each new cycle and before exit**. User edits always take
precedence.

1. **`start_campaign(planner_skill="vit-nas-planner", domain="vit-nas", title=…)`** — begins
   the run; returns a `run_id` you pass to every later call.
2. **Gather inputs** (above), then **`set_campaign_spec(run_id, spec=…)`** — persist the
   agreed spec (task/repo, variables + ranges, `efficiency_target`, platform, budget).
3. **Draft a numbered plan**, show it to the user, and on approval
   **`save_campaign_plan(run_id, plan=…)`**.
4. **Propose the cycle's search points**, then **`dispatch_cycle(run_id, candidates=[…],
   cycle=N, cluster=…)`** — each candidate is a dict of the search variables (e.g.
   `{"embed_dim": 1024, "depth": 12, "num_heads": 8, "patch_size": 8, "lr": 5e-4,
   "global_batch_size": 16, "tensor_parallel": 1, "context_parallel": 1}`). This launches one
   `vit-train` job per candidate and returns immediately. Tell the user the trainings are
   queued and that they'll be **emailed as each completes**; results are filled onto the
   campaign steps by the monitor. Long trainings that get requeued resume from the job's
   checkpoint, and the campaign itself resumes from durable state — so you can pick up later.
5. When the cycle's jobs have completed, **`get_campaign_status(run_id)`** to read the
   per-candidate results, then **score** them: write the candidates + their metrics to a JSON
   file and run

   ```bash
   python3 /mnt/skills/vit-nas-planner/scripts/score_candidates.py results.json
   ```

   The scorer computes `efficiency = throughput_samples_s / val_loss` for each candidate,
   ranks them, reports the best so far and whether the target is met, and lists any candidate
   that couldn't be scored (missing metric / failed run). Summarize this for the user
   (best-so-far, trends, failed candidates).
6. **Decide:** if the efficiency target is met and the user confirms →
   **`finish_campaign(run_id, "converged")`**. Otherwise propose the next cycle, focusing the
   search around the best region (and trading off architecture vs. parallelism for
   throughput), get approval, and go to step 4. You may decide the next points autonomously,
   but **consult the user** when the budget is nearly spent, results plateau, or you want to
   change the search space. If the user wants to stop →
   **`finish_campaign(run_id, "exited")`**.

## Scoring — efficiency-ranked

`scripts/score_candidates.py` applies (matching `campaign.yaml`):

- **Primary (ranked):** maximize **`efficiency = throughput_samples_s / val_loss`**
  (target default `≥ 185`).
- A candidate **missing `val_loss` or `throughput_samples_s`, or with non-positive
  `val_loss`**, can't be scored — it's reported as **infeasible** (e.g. a crashed or
  unfinished training), never ranked.

Input JSON shape (one entry per candidate; both metrics come from the candidate's
`vit-train` job results):

```json
{"efficiency_target": 185,
 "candidates": [
   {"params": {"embed_dim": 1024, "depth": 12, "num_heads": 8, "patch_size": 8,
               "lr": 0.0005, "global_batch_size": 16, "tensor_parallel": 1, "context_parallel": 1},
    "metrics": {"val_loss": 0.42, "throughput_samples_s": 85.0}}
 ]}
```

## Guardrails

- No HPC training without an approved plan; confirm before each new cycle and before exit.
- The user's instructions and plan edits always override your defaults.
- Keep candidates valid: `num_heads` divides `embed_dim`; `tensor_parallel × context_parallel`
  divides the GPUs per job. Skip / repair invalid points rather than dispatching them.
- Cite job ids / results for every number you report, and call out candidates whose training
  failed or didn't report metrics (counted infeasible, not as a poor score).
