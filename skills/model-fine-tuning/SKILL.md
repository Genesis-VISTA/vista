---
name: model-fine-tuning
description: >-
  Fine-tune or evaluate the FORGE-based molten-salt regression model using the
  local forge-tune workflow or Frontier HPC job submission workflow. Use when
  the user asks to train, fine-tune, resume, or evaluate a model on the molten
  salt CSV data. This skill is portable and self-contained (scripts and dataset
  are bundled under this skill folder) and can optionally submit the existing
  `forge-tune` Slurm job through MCP tools.
metadata:
  tab: molten-salt
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
- [ ] 2. Ask whether to submit on Frontier HPC (`submit_hpc_job`) or run locally
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
- For fine-tuning requests, ask a confirmation question before any HPC submission:
  "Do you want me to submit this as a Frontier job now?"
- In the first response for fine-tuning, ask only that one question and defer all
  other configuration questions until the user answers yes/no.
- Only call `submit_hpc_job(job="forge-tune", ...)` after explicit user confirmation.
- When HPC submission is chosen, rely on MCP elicitation for SSH login prompts (credential popup).
