---
name: model-fine-tuning
description: >-
  Fine-tune or evaluate the FORGE-based molten-salt regression model. Supports
  the local forge-tune workflow and HPC submission on three clusters: Odo
  (OLCF, S3M API), Frontier (OLCF, IRI compute + SSH file ops), and Perlmutter
  (NERSC, IRI). Use when the user asks to train, fine-tune, resume, or evaluate
  a model on the molten salt CSV data.
metadata:
  tags: ["OLCF", "Odo", "Frontier", "NERSC", "Perlmutter", "Materials Design", "Molten Salt Tritium Breeding"]
---

# Model Fine-Tuning

Use this skill to run or explain the local fine-tuning flow implemented in:
- `skills/model-fine-tuning/scripts/forge-tune.py`
- `skills/model-fine-tuning/scripts/hybrid_split.py`
- `skills/model-fine-tuning/assets/Molten_Salt_Thermophysical_Properties.csv`

Upstream/source copies currently also exist in `hpc_jobs/forge-tune/`.

## Workflow

Progress:
- [ ] 1. Confirm run goal (train, resume, or eval-only)
- [ ] 2. Decide which cluster. If the user's message contains a cluster name,
       USE THAT CLUSTER. No fallback, no "default to Odo", no asking again.
       Word-to-cluster mapping (exact, case-insensitive):
         - "Frontier"             → `cluster="frontier"`
         - "Perlmutter" / "NERSC" → `cluster="perlmutter"`
         - "Odo"                  → `cluster="odo"`
         - "OLCF" alone (no Frontier/Odo) → ASK which OLCF cluster
       Only if NO cluster name appears in the recent conversation, ask:
       *"Submit on Odo (OLCF), Frontier (OLCF), or Perlmutter (NERSC)?"*

       Worked example of the correct behavior:
         user: "submit a fine-tune job to Frontier"
         agent: "Submit this as a Frontier job now?"   ← uses Frontier; does NOT say "Odo"
       Wrong behavior (DO NOT do this):
         user: "submit a fine-tune job to Frontier"
         agent: "Submit this as an Odo job now?"       ← BUG: ignored what the user said
- [ ] 3. Confirm model path and dataset path
- [ ] 4. Build command or job submission args
- [ ] 5. Run and collect key metrics/artifacts
- [ ] 6. Summarize inputs, outputs, and next actions

## Inputs

Required:
- `--model` (HuggingFace model path or id; FORGE/GPT-NeoX style expected)
- `--input` CSV path

Common tuning inputs:
- `--num-epochs`
- `--batch-size`
- `--initial-lr`
- `--freeze-llm`
- `--use-differential-lr`
- `--checkpoint-dir`

For full command templates and argument references, see:
- `references/forge-tune-commands.md`

## Output Contract

The script reports:
- split sizes (`Train/Val/Test`)
- per-epoch losses/metric (RMSE for regression)
- final validation metric

## Visualizing training progress

When the user asks for job status, progress, or a plot while training is running:
1. Call `get_hpc_job_status` to fetch the latest logs.
2. Render the per-epoch table (Epoch / LR / Train RMSE / Val RMSE).
3. Plot Train RMSE and Val RMSE vs Epoch by calling `run_bash` in the sandbox
   with a short matplotlib script. Save the PNG to
   `/mnt/data/output/<job_id>/training_progress.png`, then call `display_file`
   to embed it inline.

The matplotlib script template (substitute the parsed arrays + job id):

```python
import matplotlib.pyplot as plt
epochs = [...]; train = [...]; val = [...]
fig, ax = plt.subplots(figsize=(7, 4))
ax.plot(epochs, train, marker='o', label='Train RMSE')
ax.plot(epochs, val,   marker='s', label='Val RMSE')
ax.set_xlabel('Epoch'); ax.set_ylabel('RMSE')
ax.set_title(f'forge-tune progress (job_id)'); ax.legend(); ax.grid(True, alpha=0.3)
fig.tight_layout(); fig.savefig('/mnt/data/output/<job_id>/training_progress.png', dpi=120)
```

### Live (auto-updating) watch mode

When the user asks to *watch*, *monitor*, *auto-update*, or *keep refreshing* the
job, enter a polling loop. **Each iteration of the loop MUST execute all four
tool calls below in order, with no skipping or merging — even if the data
looks unchanged from the previous cycle.** Token-savings shortcuts are NOT
acceptable here; the visible plot is the whole point of watch mode.

Per-cycle checklist (do all of these, every cycle):

1. **Call `get_hpc_job_status(job_id)`** — fetch the latest logs.
2. **Call `run_bash`** with a matplotlib script that parses the just-fetched
   epoch / Train RMSE / Val RMSE values and writes a PNG to
   `/mnt/data/output/<job_id>/training_progress.png` (overwrite each cycle).
3. **Call `display_file`** with that PNG path to embed the image inline in
   chat. This step is mandatory every cycle — `run_bash` alone does NOT
   display the image; `display_file` is what shows it to the user.
4. After all three of the above have completed, write a short text update
   (state + latest epoch) and then either:
   - If state is `ACTIVE`, `PENDING`, `QUEUED`, or `NEW`: call `run_bash`
     with the single command `sleep 45` (stays under the 60s tool timeout),
     then loop back to step 1.
   - If state is `COMPLETED`, `FAILED`, or `CANCELED`: stop and summarize.
   - Escape hatch: stop after 20 iterations regardless.

If you find yourself tempted to skip step 2 or 3 because "the plot didn't
change much," do not — re-run them anyway. The user is watching the chat for
periodic plot updates; missing cycles look like the agent stopped working.

Notes:
- Iteration budget is governed by the project's `usage_limits` (Pydantic AI
  `UsageLimits`, stored in the project DB row). For long watch loops, ensure
  `request_limit` is at least ~100 (≈3 tool calls per poll × 30 polls).
- The user can interrupt the loop at any time by sending another message.

Artifacts:
- checkpoint files in `--checkpoint-dir`:
  - `checkpoint_latest.pt`
  - `checkpoint_best.pt`
  - periodic `checkpoint_epoch_*.pt` (every 5 epochs)
- logs in `--checkpoint-dir`:
  - `training_speed_log.csv`
  - `gpu_memory_log.csv` (when CUDA is used)
- final saved model:
  - `<model_name>_classifier.pt`

## Guardrails

- Treat this as regression by default unless user explicitly requests classification.
- For `--eval-only`, require `--resume-from <checkpoint_path>`.
- Keep commands explicit and reproducible; include all non-default args in the final command.
- Resolve the target cluster from the user's words BEFORE composing any
  confirmation question. See workflow step 2 for the mapping. NEVER default
  to Odo. NEVER substitute a different cluster name into the confirmation
  prompt than the one the user named.
- Confirmation template: *"Submit this as a {} job
  now?"* — substitute {} with exactly one of those three names (Odo, Frontier, Perlmutter), the one matching
  workflow step 2's resolution. If the user said "Frontier", the question
  MUST say "Frontier", not "Odo".
- In the first response for fine-tuning, ask only the cluster question (if
  needed) plus the confirmation, and defer all other configuration questions
  until the user answers.
- Only call `submit_hpc_job(job="forge-tune", cluster="odo" | "frontier" | "perlmutter", ...)`
  after explicit user confirmation.
- The MCP elicitation modal will pop up a final "Confirm running on <cluster>"
  check — the user can still cancel there.
