---
name: llm-pretraining
description: >-
  Pre-train a FORGE scientific LLM (GPT-NeoX / DeepSpeed; forge-s, forge-m, or
  forge-l) from scratch on the FORGE tokenized scientific corpus, on Lux or
  Frontier (OLCF). Submits the forge-pretrain HPC job, then
  monitors loss and throughput with live plots, and cancels on request. Use when
  the user asks to pre-train, train from scratch, or benchmark training of a
  FORGE / GPT-NeoX language model.
metadata:
  tags: ["OLCF", "Lux", "Frontier", "LLM", "Pre-training", "GPT-NeoX", "DeepSpeed"]
---

# LLM Pre-Training (FORGE on Lux and Frontier)

Runs the `forge-pretrain` HPC job: the `lux` branch of
https://github.com/at-aaims/forge, updated to its latest commit on every
submission, training a FORGE model on the FORGE scientific corpus. It was set up under
project stf218 on Lux and chm243 on Frontier. It trains a fixed number of
iterations (default 50), which makes it a scaling/throughput run or the first
leg of a longer one, not a full pre-training campaign.

Out of scope here: converting checkpoints to HuggingFace, evaluation, text
generation, and fine-tuning (for fine-tuning use the `model-fine-tuning` skill).

## Workflow

Progress:
- [ ] 1. Resolve the cluster.
- [ ] 2. Resolve the run configuration (model, nodes, duration, iterations).
- [ ] 3. Confirm, then submit.
- [ ] 4. Monitor: status, loss/throughput table, plot.
- [ ] 5. Summarize results and next steps.

### 1. Cluster

If the user's message names a cluster, USE THAT CLUSTER. Do not substitute the
other one, and do not ask again:
- "Lux" → `cluster="lux"`
- "Frontier" → `cluster="frontier"`
- "OLCF" alone → ask: *"Submit on Lux or Frontier?"*
- Odo or Perlmutter → say this job does not run there, and offer Lux or Frontier.
- No cluster named → ask: *"Submit on Lux or Frontier?"*

Always pass `cluster` explicitly: Lux has no token and is never picked by
default, and a user with several OLCF tokens has no single default either.

What each cluster needs from the user:

- **Lux:** an OLCF account on Lux, and a **Lux remote directory** in their VISTA
  settings. The job is charged to their default Slurm account. Logging in is
  interactive (see step 3).
- **Frontier:** the same setup as fine-tuning on Frontier:
  - An S3M token in their VISTA settings. The job is charged to that token's
    project.
  - Globus connected for Frontier (VISTA settings → File transfer).
  - A **Frontier remote directory** in their VISTA settings, writable by the
    project's group.
- If submission fails, show the message as it is. It says what to set or fix:
  a token, a remote directory, or that directory's permissions.

### 2. Run configuration

Everything has a default, so do not quiz the user. Take what they said, and fill
in the rest from the defaults:

| Setting | How it is passed | Default |
|---|---|---|
| Model architecture | `script_args` `MODEL=forge-s\|forge-m\|forge-l` | Lux: `forge-l`; Frontier: `forge-s` |
| Nodes (8 GPUs each) | `node_count` | 16 |
| Walltime | `duration` (`"h:mm:ss"`) | `0:30:00` |
| Training iterations | `script_args` `TRAIN_ITERS=<n>` | 50 |
| Checkpoint interval | `script_args` `SAVE_INTERVAL=<n>` (`0` = none) | `0` |
| Log interval | `script_args` `LOG_INTERVAL=<n>` | 1 |
| LR decay horizon | `script_args` `LR_DECAY_ITERS=<n>` | model config's |
| Resume from | `script_args` `LOAD_DIR=<checkpoints dir>` | none (fresh start) |

Map the user's words to a model: "small" → `forge-s`, "medium" → `forge-m`,
"large" → `forge-l`. See `references/forge-models.md` for the architectures and
how to size a run.

The default model depends on the cluster, because Frontier's GPUs have less
memory than Lux's: **forge-l on Lux, forge-s on Frontier**. The job applies this
default itself, so leave `MODEL` out unless the user picked a model. If the user
asks for forge-m or forge-l on Frontier, submit what they asked for, but say
first that it may run out of GPU memory there, and that Lux or more nodes are
the alternatives.

`script_args` is a single space-separated string of `KEY=VALUE` pairs, e.g.
`script_args="MODEL=forge-m TRAIN_ITERS=100"`. Pass only the keys that differ
from the defaults. Any other key makes the job exit immediately.

Limits: `node_count` ≤ 64, `duration` ≤ `4:00:00`.

Checkpoints are **off** by default. A forge-l checkpoint with optimizer state is
hundreds of GB and takes minutes to write. Only set `SAVE_INTERVAL` when the user
asks for checkpoints or to resume later, and then mention the size. For one
checkpoint at the end, set `SAVE_INTERVAL` equal to `TRAIN_ITERS`.

### 3. Confirm, then submit

Confirm in one line with the resolved values, including the model the cluster's
default gives, and the cluster from step 1, e.g.:

*"Submit forge-l pre-training on Lux: 16 nodes, 0:30:00, 50 iterations, no
checkpoint?"*

*"Submit forge-s pre-training on Frontier: 16 nodes, 0:30:00, 50 iterations, no
checkpoint?"*

Only after the user says yes, call (defaults need no `script_args`):

```
submit_hpc_job(job="forge-pretrain", cluster="lux",
               node_count=16, duration="0:30:00")
```

With choices, e.g. forge-m for 100 iterations on Frontier:

```
submit_hpc_job(job="forge-pretrain", cluster="frontier",
               node_count=16, duration="0:30:00",
               script_args="MODEL=forge-m TRAIN_ITERS=100")
```

**On Lux**, before the first Lux call in a chat, tell the user about the login:

- Two login prompts appear: first `hub.ccs.ornl.gov`, then the Lux login node.
  Each asks for their OLCF username and **PIN + RSA passcode**.
- The second prompt needs a **new** passcode. Wait for the token to change; the
  one used for the hub will not work again.
- After that, the login is reused for every Lux call in the chat (status,
  outputs, cancel) for about an hour after the last call.

The forge checkout is updated to the latest commit on every submission:
- **On Lux**, this happens on the login node before `sbatch`. If it fails (proxy,
  git, missing data), the tool returns the error text. Show it to the user
  rather than retrying blindly.
- **On Frontier**, there is no login step. The update runs at the start of the
  job, so a checkout problem shows up as a failed job, with the reason in its
  log (lines starting `[prepare_forge]`).

Report the tool's returned summary verbatim: job_id, cluster, nodes, duration.

### 4. Monitor

`get_hpc_job_status(job_id=..., cluster=...)` returns:
- `STATE`: `NEW`, `QUEUED`, `PENDING`, `ACTIVE`, `COMPLETED`, `FAILED`, or
  `CANCELED`.
- On Lux only:
  - `SLURM_STATE`: the raw Slurm state, e.g. `TIMEOUT` or `NODE_FAIL`, which
    says *why* a job is `FAILED`.
  - `REASON`: why a pending job is waiting.
- On Frontier, `EXIT_CODE` and `MESSAGE` when the job has finished.
- The tail of the log, and the output files.

The status call also saves the **whole** log so far to
`/mnt/data/output/<job_id>/log-<job_id>.out` in the sandbox. Parse and plot from
that file with the bundled script rather than from the tail:

```bash
MPLBACKEND=Agg python3 /mnt/skills/llm-pretraining/scripts/plot_training.py \
  --log /mnt/data/output/<job_id>/log-<job_id>.out \
  --output /mnt/data/output/<job_id>/training_progress.png \
  --title "<model> on <cluster> (job <job_id>)"
```

It writes the PNG (loss vs iteration on the left; TFLOPS per GPU and samples/sec
on the right), a CSV next to it, and prints a JSON summary:
- `last_iteration` / `total_iters`
- `first_lm_loss` / `last_lm_loss`
- `median_ms_per_iter`, `median_tflops_per_gpu`, `median_samples_per_sec`
  (the first logged iteration is left out, since it includes warm-up)
- `skipped_iterations`, `nan_iterations`
- `eta_seconds`

Then call `display_file` on the PNG to show it inline, and report the key
numbers from the summary in a short table.

Early in a run, before the first iteration line, the log shows setup: the forge
commit, `rocm-smi`, config merge, NeoX argument dump, and dataset index
building. On the first run of a given shape (iterations × global batch), rank 0
builds the dataset index maps for the whole corpus, which can take several
minutes. Later runs of the same shape reuse them. No iteration lines yet
therefore does not mean the job is stuck.

#### Watch mode

When the user asks to *watch*, *monitor*, *auto-update*, or *keep refreshing*,
loop. **Every cycle runs all of these tool calls, in order, even if nothing
seems to have changed**, because the refreshed plot is the point:

1. `get_hpc_job_status(job_id, cluster=<cluster>)`
2. `run_bash`: the `plot_training.py` command above (it overwrites the PNG).
3. `display_file` on the PNG. `run_bash` alone does not show the image.
4. A short text update: state, iteration x/y, latest loss, TFLOPS/GPU, ETA. Then:
   - `NEW`, `QUEUED`, `PENDING`, or `ACTIVE`: `run_bash` with `sleep 45`, then
     back to step 1.
   - `COMPLETED`, `FAILED`, or `CANCELED`: stop and summarize.
   - Stop after 20 cycles regardless.

The user can interrupt at any time by sending a message. A long watch needs the
project's `request_limit` at about 100 or more (≈4 calls per cycle).

### 5. Results

On completion, summarize:
- model
- nodes and GPUs (nodes × 8)
- iterations completed
- first and last training loss
- median ms/iteration, TFLOPS per GPU and samples/sec
- skipped or NaN iterations, if any

The output directory holds:
- `config/<MODEL>.yml`: the exact merged config trained
- `checkpoints/`, if enabled
- `tensorboard/` and `logs/`

Fetch small files with `get_hpc_job_outputs(job_id, files=[...], cluster=<cluster>)`,
e.g. `config/<model>.yml`. Leave checkpoints on the cluster.

On failure, use the state fields (`SLURM_STATE` on Lux; `EXIT_CODE` and `MESSAGE`
on Frontier) and the log tail:
- `TIMEOUT`: walltime too short for the iterations. Suggest a longer `duration`
  or fewer `TRAIN_ITERS`.
- An out-of-memory error: suggest more nodes, or a smaller model.
- `NODE_FAIL`: resubmit.
- Anything else: show the last error lines from the log.

To resume, resubmit with `LOAD_DIR=<previous output dir>/checkpoints`. This only
works if that run saved checkpoints. Give a larger `TRAIN_ITERS`, since NeoX
continues from the saved iteration.

## Cancel

`cancel_hpc_job(job_id, cluster=<cluster>)` after the user confirms. On Lux it
reuses the cached login.

## Guardrails

- Never submit without explicit user confirmation of the resolved values.
- Always `job="forge-pretrain"`, and the cluster resolved in step 1: `"lux"` or
  `"frontier"`. Never substitute one for the other in the confirmation or the
  call.
- Never invent a job id, loss, or throughput number. Report what the tools and
  the plot script return.
- Do not enable checkpoints unless asked. When asked for forge-l, say how large
  they are.
- Do not retry a failed submission more than once without asking the user. A
  login or setup failure usually needs them (passcode, access, proxy).
