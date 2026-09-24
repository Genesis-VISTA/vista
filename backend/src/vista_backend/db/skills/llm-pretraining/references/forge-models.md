# FORGE model architectures and run sizing

The configs live in the forge `lux` branch, under `train/configs/<MODEL>.yml`. The
run's exact merged config is written to `<output dir>/config/<MODEL>.yml`, so
fetch that file with `get_hpc_job_outputs` for authoritative values.

| Model | Layers | Hidden | Heads | Seq len | Model-parallel | Micro-batch / GPU | Grad. accum. |
|---|---|---|---|---|---|---|---|
| forge-s | 24 | 2064 | 24 | 2048 | 1 | 16 | 1 |
| forge-m | 40 | 5120 | 40 | 2048 | 2 | 12 | 2 |
| forge-l | 48 | 6144 | 48 | 2048 | 2 | 16 | 1 |

Approximate parameter counts (12·L·h², excluding embeddings): forge-s ≈ 1.2B,
forge-m ≈ 13B, forge-l ≈ 22B.

forge-l is the config tuned for Lux: its upstream settings are `train-iters` 50
and `log-interval` 1. forge-s and forge-m have `train-iters` 15300 upstream. Here
every model trains `TRAIN_ITERS` (default 50) iterations.

## Global batch and data parallelism

- GPUs = nodes × 8
- data-parallel size = GPUs / model-parallel
- global batch (sequences) = data-parallel size × micro-batch × grad. accum.

Example: forge-l on 16 nodes gives 128 GPUs, data-parallel 64, and a global
batch of 64 × 16 = 1024 sequences, which is about 2.1M tokens per iteration.

More nodes means a larger global batch, not a shorter iteration. Keep that in
mind when comparing loss across runs of different sizes.

## Choosing a walltime

- Budget a few minutes of fixed startup:
  - environment
  - DeepSpeed op builds (once, then cached)
  - dataset index maps (once per run shape, then cached)
- Then add `TRAIN_ITERS` × seconds per iteration.
- Take seconds per iteration from `median_ms_per_iter` of an earlier run of the
  same model and node count.
- Without one, keep the default 30 minutes for 50 iterations, and size later
  runs from the first run's numbers.
- A checkpoint save adds minutes for forge-l.

## Log lines

The training loop prints one line per `LOG_INTERVAL` iterations (rank 0):

```
 samples/sec: 51.234 | iteration       10/      50 | elapsed time per iteration (ms): 19987.6 |
 learning rate: 1.234E-04 | approx flops per GPU: 150.3TFLOPS | lm_loss: 7.123456E+00 |
 number of skipped iterations:   0 | number of nan iterations:   0 |
```

Validation, when `eval-interval` is reached:

```
 validation results at iteration 100 | lm_loss value: 6.912345E+00 | lm_loss_ppl value: 1.004E+03 |
```

`scripts/plot_training.py` parses both.
