# Forge-Tune Commands (Local, No Slurm)

Portable/self-contained base directory:

```bash
cd skills/model-fine-tuning
```

Default data file:

```text
./assets/Molten_Salt_Thermophysical_Properties.csv
```

## 1. Train (frozen LLM baseline)

```bash
python3 ./scripts/forge-tune.py \
  --input ./assets/Molten_Salt_Thermophysical_Properties.csv \
  --model /path/to/forge-model \
  --num-epochs 20 \
  --batch-size 4 \
  --initial-lr 1e-5 \
  --freeze-llm \
  --checkpoint-dir ./checkpoints-freeze
```

## 2. Train (end-to-end with differential LR)

```bash
python3 ./scripts/forge-tune.py \
  --input ./assets/Molten_Salt_Thermophysical_Properties.csv \
  --model /path/to/forge-model \
  --num-epochs 20 \
  --batch-size 4 \
  --initial-lr 1e-5 \
  --use-differential-lr \
  --checkpoint-dir ./checkpoints-e2e
```

## 3. Resume training

```bash
python3 ./scripts/forge-tune.py \
  --input ./assets/Molten_Salt_Thermophysical_Properties.csv \
  --model /path/to/forge-model \
  --num-epochs 50 \
  --resume-from ./checkpoints-e2e/checkpoint_latest.pt \
  --checkpoint-dir ./checkpoints-e2e
```

## 4. Eval only

```bash
python3 ./scripts/forge-tune.py \
  --input ./assets/Molten_Salt_Thermophysical_Properties.csv \
  --model /path/to/forge-model \
  --eval-only \
  --resume-from ./checkpoints-e2e/checkpoint_best.pt
```

## Key CLI Arguments

- `--input`: input CSV
- `--model`: HF model path/id
- `--emb-size`: embedding size (default `2064`)
- `--seq-len`: sequence length (default `128`)
- `--batch-size`: batch size (default `4`)
- `--num-epochs`: epochs (default `100`)
- `--freeze-llm`: freeze foundation model and only train head
- `--use-differential-lr`: lower LR for LLM, higher LR for head
- `--checkpoint-dir`: checkpoint/log output directory
- `--resume-from`: checkpoint path to load
- `--eval-only`: run evaluation without training (requires `--resume-from`)
- `--task`: `regression` or `classification` (default `regression`)
- `--n-outputs`: output dimension/classes (default `1`)
- `--warmup-epochs`: LR warmup epochs
- `--min-lr`: minimum LR for cosine schedule
- `--initial-lr`: initial LR (default behavior is `1e-5` if omitted)

## Data-Split Notes

- Regression mode uses `hybrid_split_salt_data` from `hybrid_split.py`.
- Current settings in `forge-tune.py`:
  - `test_formula_frac=0`
  - `val_formula_frac=0.15`
  - `val_composition_frac=0.15`
  - `min_train_compositions=2`

## Source-of-Truth Note

- This skill is intentionally self-contained for portability.
- Upstream/source files are also present at `hpc_jobs/forge-tune/`; if those change, sync both copies.
