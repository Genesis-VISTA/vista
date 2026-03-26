---
name: model-fine-tuning
description: >-
  Fine-tune or evaluate the FORGE-based molten-salt regression model using the
  local forge-tune workflow. Use when the user asks to train, fine-tune,
  resume, or evaluate a model on the molten salt CSV data. This skill is
  portable and self-contained (scripts and dataset are bundled under this skill
  folder) and does not submit Slurm jobs.
---

# Model Fine-Tuning

Use this skill to run or explain the local fine-tuning flow implemented in:
- `skills/model-fine-tuning/scripts/forge-tune.py`
- `skills/model-fine-tuning/scripts/hybrid_split.py`
- `skills/model-fine-tuning/assets/Molten_Salt_Thermophysical_Properties.csv`

Upstream/source copies currently also exist in `hpc_jobs/forge-tune/`.

Do not use Slurm submission steps from this skill.

## Workflow

Progress:
- [ ] 1. Confirm run goal (train, resume, or eval-only)
- [ ] 2. Confirm model path and dataset path
- [ ] 3. Build command
- [ ] 4. Run and collect key metrics/artifacts
- [ ] 5. Summarize inputs, outputs, and next actions

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
- If a user asks for cluster submission, pause and note this skill intentionally excludes Slurm submission.
