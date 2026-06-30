---
name: vit-train
description: >-
  Train one Vision Transformer (ViT) configuration on HPC and report its validation loss and
  training throughput — the per-candidate worker for a ViT-NAS campaign. Given a search point
  (embedding size, depth, heads, patch size, learning rate, global batch size, and tensor /
  context parallel degrees), it launches one training run of the public climate-vit model
  (ERA5 weather forecasting) on Frontier or Perlmutter GPUs and returns val_loss +
  throughput_samples_s. Use this skill whenever a planner needs to evaluate a single ViT
  configuration by actually training it on HPC; the default model is climate-vit but the job
  generalizes to any ViT training repo that logs validation loss and samples/sec. In a
  multi-agent campaign this is the `training` sim skill — one subagent per search point, the
  worker the ViT-NAS planner fans out.
metadata:
  version: "0.1.0"
  tags: ["Vision Transformer", "Neural Architecture Search", "HPC", "Training", "Weather"]
license: Proprietary
author: VISTA team
---

# ViT train: validation loss + throughput for one ViT configuration

This skill answers "how good and how fast is *this* ViT configuration?" by **training it** on
HPC GPUs for a short screening budget and reading back two numbers:

- **`val_loss`** — validation loss (lower is better), from the training log line
  `Avg val loss=<value>`.
- **`throughput_samples_s`** — training throughput in samples/sec (higher is better), from
  `... avg <value> samples/sec`.

The planner combines them into an efficiency score (`throughput_samples_s / val_loss`) and
searches the configuration space. One submission trains one search point and writes one
`results.json`.

## The code lives in a public repo — the job clones it

The training implementation (`train_mp.py`, `config/ViT.yaml`, `export_DDP_vars.sh`, the data
loaders) is **not vendored into VISTA**. It is a public GitHub repo the HPC job clones at
runtime:

> **`https://github.com/jqyin/climate-vit`**  (ViT for ERA5 weather forecasting)

VISTA carries only this SKILL.md and the thin HPC job (`hpc_jobs/vit-train`) that bootstraps
the repo on the compute node, renders a config from the candidate, and launches training.

**This is a GPU/HPC-only skill** — there is no cheap sandbox path (a real training run needs
the GPUs and the ERA5 dataset). The default trains climate-vit on ERA5, but the job is
generic: point `CLIMATEVIT_REPO_URL` / `CLIMATEVIT_DATA_ROOT` at another ViT repo + dataset to
reuse it, as long as that repo logs the same two metric lines.

## The key mapping: search point → ViT.yaml + launcher flags

climate-vit takes its ViT hyperparameters from `config/ViT.yaml`, **not** the command line, so
the job renders a config block (`config: nas`) from the candidate's architecture/lr/batch keys
and passes the parallelism degrees as flags to `train_mp.py`:

| Candidate key | Where it goes |
|---|---|
| `embed_dim`, `depth`, `num_heads`, `patch_size`, `lr`, `global_batch_size` | rendered into `ViT.yaml` (override the repo's `base`) |
| `tensor_parallel`, `context_parallel` | `--tensor_parallel` / `--context_parallel` on `train_mp.py` |

Constraints: `num_heads` must divide `embed_dim`; `tensor_parallel × context_parallel` must
divide the GPUs in the allocation. `run_state_point.py` validates these and fails fast.

## Running the training on HPC (the HPC step)

The run is wrapped as the VISTA HPC job **`vit-train`**, with GPU backends for **Frontier**
(OLCF, default — ERA5 is on OLCF world-shared) and **Perlmutter** (NERSC) — pick with
`cluster=`. One submission = one search point = one `results.json`. The job clones the repo,
loads the GPU stack, renders the config, launches `srun train_mp.py` across the node's GPUs,
scrapes the log, and writes `results.json` into the job's output dir. See
`hpc_jobs/vit-train/README.md` for the script-args and tunables.

## Sim-skill contract for multi-agent campaigns

In a VISTA campaign, the planner fans out **one training subagent per candidate**; each
subagent uses this skill to launch and parse one `vit-train` job. The two operations a
subagent performs:

- **dispatch(order) → job:** submit the candidate's training,
  `submit_hpc_job(job="vit-train", cluster="frontier"|"perlmutter", duration="1:00:00",
  script_args=<candidate JSON>)`, and record the returned `job_id` + `cluster`. The candidate
  JSON is the campaign wire format — passed through verbatim; the job decodes it.
- **collect(job) → result:** once the job completes,
  `get_hpc_job_outputs(... files=["results.json"])` and parse it into the structured result.
  `results.json` already carries the metrics:
  ```json
  {"params": {...the candidate...},
   "metrics": {"val_loss": <float>, "throughput_samples_s": <float>},
   "geometry": {"nnodes": 1, "gpus_per_node": 8, "ntasks": 8,
                "tensor_parallel": 1, "context_parallel": 1},
   "config": "nas", "num_iters": 2000, "expdir": "<run dir>"}
  ```
  Return a `ParsedResult` with `metrics = {"val_loss": …, "throughput_samples_s": …}` and
  `ok = true`. If `results.json` is missing, the run failed, or **either** metric is null
  (training crashed or didn't reach a validation step), set `ok = false` and explain in
  `summary` — the planner's scorer treats a candidate without both metrics as infeasible,
  not as a poor score.

Subagents do not talk to each other; the planner and durable campaign state coordinate them.
To screen a batch, the planner issues N candidates (one per search point) and dispatches N
`vit-train` jobs in parallel, then collects and hands the results to the planner's scorer for
ranking. Long trainings that get requeued resume from the job's durable `expdir`; the campaign
itself resumes from durable state and emails the user as each job finishes.
