# forge-pretrain

Pre-train a FORGE scientific LLM (GPT-NeoX / DeepSpeed) from scratch on the FORGE
tokenized scientific corpus. Code is the `lux` branch of
https://github.com/at-aaims/forge, cloned and updated to the latest commit on
every submission. Runs under the csc708 OLCF project.

Supported clusters: Lux (OLCF, Slurm over SSH; the user is asked to log in through
the hub once per session). Defaults: forge-l, 16 nodes x 8 GPUs, 30 minutes, 50
training iterations, no checkpoint.

## Script args

`script_args` is a space-separated list of `KEY=VALUE` pairs. All are optional:

- `MODEL=forge-s|forge-m|forge-l`: model architecture (default `forge-l`).
  forge-s: 24 layers, hidden 2064, model-parallel 1. forge-m: 40 layers, hidden
  5120, model-parallel 2. forge-l: 48 layers, hidden 6144, model-parallel 2.
- `TRAIN_ITERS=<n>`: training iterations (default 50).
- `SAVE_INTERVAL=<n>`: checkpoint every n iterations (default `0`: no checkpoint).
  Set it to `TRAIN_ITERS` for one checkpoint at the end. A forge-l checkpoint with
  optimizer state is hundreds of GB and takes a while to write.
- `LOG_INTERVAL=<n>`: log loss and throughput every n iterations (default 1).
- `LR_DECAY_ITERS=<n>`: learning-rate decay horizon (default: the model config's).
- `LOAD_DIR=<path>`: resume from the checkpoints in this directory, e.g. an
  earlier run's `<output dir>/checkpoints`.

Example: `script_args="MODEL=forge-m TRAIN_ITERS=100"`.

## Outputs

Written under the job's output directory:

- `config/<MODEL>.yml`: the merged config actually trained.
- `checkpoints/`: DeepSpeed checkpoints (`global_step<N>/`, `latest`), if enabled.
- `tensorboard/`, `logs/`: NeoX logs.
- `hostfile`: the nodes used.

Loss and throughput are printed in the job log (`log-<jobid>.out`) as lines like
`iteration 10/ 50 | ... | lm_loss: 7.1234E+00 | ...`.
