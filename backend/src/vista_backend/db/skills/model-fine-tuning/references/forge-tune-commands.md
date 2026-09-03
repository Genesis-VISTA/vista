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
- `--checkpoint-dir`: where checkpoints and the final classifier are written
- `--log-dir`: where the CSV logs are written (default: `--checkpoint-dir`). The
  vista job scripts point this at `$VISTA_OUT` and `--checkpoint-dir` at
  `$VISTA_KEEP`, so the small logs come back with the job output while the
  multi-GB checkpoints stay on the cluster.
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

## HPC submission (via vista MCP tools)

The local commands above are for ad-hoc runs. For real training, submit via
`submit_hpc_job` and let vista handle Slurm + file paths. Three clusters supported:

```text
# Odo (OLCF Frontier-class training system) — defaults from "odo" section, IRI compute + S3 output push
submit_hpc_job(job="forge-tune", cluster="odo", duration="0:30:00")

# Frontier (OLCF production) — defaults from "frontier" section, IRI compute + S3 output push
submit_hpc_job(job="forge-tune", cluster="frontier", duration="0:30:00")

# Perlmutter (NERSC) — defaults from "perlmutter" section, runs under shifter
submit_hpc_job(job="forge-tune", cluster="perlmutter", duration="0:30:00")
```

Per-cluster defaults (nodes, duration, image, queue, etc.) live in
`hpc_jobs/forge-tune/cluster_defaults.json`. Override at the call site with
`node_count=` and `duration=`; deeper overrides (env, image, queue) go in the JSON.

After submitting, use `get_hpc_job_status(job_id)` to watch progress and
`get_hpc_job_outputs(job_id, files=[...])` to pull artifacts back into the
sandbox at `/mnt/data/output/<job_id>/`. Only what the job wrote to `$VISTA_OUT`
is retrievable — for forge-tune that is the CSV logs, not the checkpoints. See
SKILL.md's Artifacts section.

## Source-of-Truth Note

- This skill is intentionally self-contained for portability.
- Upstream/source files are also present at `hpc_jobs/forge-tune/`; if those change, sync both copies.
