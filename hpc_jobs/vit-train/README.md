# vit-train

One training run of a Vision Transformer for one search point of a ViT-NAS campaign. The
default model is the public **climate-vit** ViT (ERA5 weather forecasting), but the job is
generic: point `CLIMATEVIT_REPO_URL` / `CLIMATEVIT_DATA_ROOT` at another ViT training repo +
dataset to reuse it, as long as that repo logs `Avg val loss=` and `avg N samples/sec`.

It ships GPU backends for **Frontier** (OLCF moderate enclave; the default — ERA5 lives on
OLCF world-shared) and **Perlmutter** (NERSC) — pick with `cluster=`. One submission = one
search point = one `results.json`. The job clones the training repo at runtime, loads the
GPU software stack (`xforge` module on Frontier / `pytorch` module on Perlmutter, or a conda
env prefix via `CLIMATEVIT_ENV`), then runs `run_state_point.py` on the head node, which
renders a `ViT.yaml` config from the candidate and launches `srun train_mp.py` across the
node's GPUs.

No training code is vendored into VISTA — `train_mp.py`, `config/ViT.yaml`, and
`export_DDP_vars.sh` all come from the clone (https://github.com/jqyin/climate-vit).

Default nodes: 1 (8 GPUs on Frontier, 4 on Perlmutter)
Default time: 1:00:00 (clone + a `CLIMATEVIT_NUM_ITERS`-iteration screening run)

## Script args (one ViT search point)

The campaign planner passes the candidate as a **single JSON object** (the campaign wire
format, `campaign.planner.encode_candidate_args`), forwarded verbatim to
`run_state_point.py`. Recognized keys:

- ViT.yaml config fields (override the repo's `base` config): `embed_dim`, `depth`,
  `num_heads`, `patch_size`, `lr`, `global_batch_size`
- launcher / parallelism: `tensor_parallel`, `context_parallel`
  (`tensor_parallel × context_parallel` must divide the GPUs in the allocation)

`num_heads` must divide `embed_dim`. Example:

    submit_hpc_job(job="vit-train", cluster="frontier", duration="1:00:00",
        script_args='{"embed_dim": 1024, "depth": 12, "num_heads": 8, "patch_size": 8,
                      "lr": 5e-4, "global_batch_size": 16, "tensor_parallel": 1, "context_parallel": 1}')

## Outputs (in $VISTA_OUT)

- `results.json` — the metrics the planner scores:
  `metrics.val_loss` (from `Avg val loss=`) and `metrics.throughput_samples_s`
  (from `avg N samples/sec`), plus the echoed `params`, the launch `geometry`, and `expdir`.
- `ViT.nas.yaml` — the rendered config block (`config: nas`).
- `train.log` — captured training stdout.
- `expdir/` — the durable run/checkpoint dir (under `$VISTA_OUT`), so a requeued training can
  resume and the campaign keeps its artifacts across the HPC queue wait.

## Tunables (cluster_defaults.json `iri.environment`)

- `CLIMATEVIT_REPO_URL` / `CLIMATEVIT_REPO_REF` — the training repo + ref to clone.
- `CLIMATEVIT_DATA_ROOT` — ERA5 dataset root (`train/ valid/ test/ stats/`). Defaults to the
  OLCF world-shared path; **must** be set to a NERSC path for Perlmutter.
- `CLIMATEVIT_NUM_ITERS` — iterations per candidate (screening budget; default 2000).
- `GPUS_PER_NODE` — geometry (8 Frontier / 4 Perlmutter).
- `CLIMATEVIT_ENV` — optional conda env prefix to activate instead of the module stack;
  `CLIMATEVIT_MODULE` / `CLIMATEVIT_MODULEPATH` override the Frontier module otherwise.

## Prerequisites

Standard credentials for the target cluster — for **Frontier**, a Frontier S3M token + the
deployment's Frontier Globus refresh token; for **Perlmutter**, a NERSC IRI token plus
`nersc_account` / `nersc_remote_dir` in the user settings, and ERA5 staged to a NERSC
filesystem (`CLIMATEVIT_DATA_ROOT`). The GPU stack comes from the cluster's module (or a
conda env you provision and pass via `CLIMATEVIT_ENV`).
