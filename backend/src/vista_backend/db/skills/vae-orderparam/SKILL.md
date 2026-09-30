---
name: vae-orderparam
description: >-
  Train a variational autoencoder on Monte Carlo configurations of a high-entropy alloy
  and export it as TorchScript, giving a learned order parameter for the order-disorder
  transition and the surrogate that DeepThermo's Wang-Landau sampler uses to propose
  global moves. Use this when the user wants a data-driven or learned order parameter,
  wants to train a VAE on MC snapshots or lattice configurations, asks how to
  distinguish ordered from disordered alloy states without hand-picking a symmetry, or
  needs to produce the encoder/decoder model that a DeepThermo Wang-Landau run consumes.
  Works for MoNbTaW and any substitutional alloy whose configurations were collected with
  the deepthermo-wl engine. This is the middle stage of a three-stage pipeline: collect
  configurations, train the VAE, then sample with it.
metadata:
  version: "0.1.0"
  tags: ["Materials Design", "High Entropy Alloy Design", "Machine Learning", "HPC", "OLCF"]
license: MIT
author: VISTA Team
---

# VAE order parameter for high-entropy alloys

Fits a VAE to lattice configurations from Monte Carlo and exports it as TorchScript. The
trained encoder is **two things at once**:

- a **learned order parameter** — latent coordinates that separate ordered from
  disordered configurations without committing in advance to a particular symmetry, and
- the **surrogate** DeepThermo's Wang-Landau loop uses to propose large, physically
  plausible moves (paper: Yin, Wang & Shankar, *DeepThermo*, IPDPS 2023).

## Where this sits

Three stages, three separate jobs, one shared workspace:

```
deepthermo-wl --mode collect   →  snap_*.xyz          (configurations)
vae-orderparam                 →  encoder/decoder.pt  (THIS SKILL)
deepthermo-wl --mode sample    →  WL density of states
```

Each stage is a separate HPC submission, so **run them in order and wait for each**.
Pass the **same `--workspace` name** to all three; that shared directory is how the
snapshots reach training and the model reaches the sampler. This skill fails immediately
if the workspace has no snapshots yet.

## The code lives in public repos — cloned at runtime

Nothing is vendored into VISTA. The job clones
**`https://github.com/jqyin/DeepThermo-WL`** (branch `vae-proposal`) **with its
`vae-modeling` submodule** (`https://code.ornl.gov/jqyin/deepthermo`, branch `torch`),
which is where the training code lives. Both are public.

## Running it

```python
submit_hpc_job(
    job="vae-orderparam",
    cluster="odo",                     # or "frontier"
    script_args="--workspace tc-n10 --n 10 --epochs 100 --order-parameter",
)
```

**Pass `--workspace NAME` in `script_args`**, and use the SAME name for all three stages —
that shared directory is how each stage's output reaches the next. It is a flag rather
than a setting because `submit_hpc_job` passes no environment.

### `script_args` contract

| Flag | Meaning | Default |
|---|---|---|
| `--workspace NAME` | shared workspace; same name across all three stages | `default` |
| `--n N` | lattice N the snapshots came from | 10 |
| `--elements Mo,Nb,Ta,W` | **must match the collect stage's element order** | MoNbTaW |
| `--alloy-tag TAG` | names the exported files | MoNbTaW |
| `--skip-frames K` | drop the first K frames per file (unequilibrated) | 50 |
| `--n-ranks K` | PT ranks that produced the snapshots (stratifies the split) | 8 |
| `--epochs K` / `--batch-size K` / `--lr F` / `--seed K` | training knobs | 100 / 32 / 5e-4 / 6 |
| `--latent-dim K` | the order parameter's dimensionality | 3 |
| `--order-parameter` | also compute the order parameter over the frames | off |

## Sim-skill contract

- **dispatch(order) → job:** turn (lattice N, elements, training knobs) into the
  `script_args` string, `submit_hpc_job(job="vae-orderparam", …)`, record `job_id` +
  `cluster`.
- **collect(job) → result:** fetch `results.json` and parse:
  ```json
  {"encoder": "<path>", "decoder": "<path>", "grid_size": 32,
   "frames_unique": 2302, "duplicate_fraction": 0.18,
   "final_train": 1076.2, "final_val": 1088.9, "train_val_gap": 0.012}
  ```

## Reading the result honestly

- **Watch the train/val *gap*, not the absolute loss.** The epoch-1 training figure is an
  initialisation transient — sum-reduced BCE over `grid³ × n_elements` voxels plus an
  early KL spike — and recovers within one epoch. A gap of a few percent means no
  overfitting; a widening gap means too little data.
- **`duplicate_fraction` is a physics diagnostic, not bookkeeping.** Cold replicas repeat
  configurations because their dynamics freeze. A high fraction means the *ordered,
  low-energy* states — the ones the order parameter most needs to resolve — are the
  thinnest part of the training set. Say so, and suggest a denser PT ladder, a larger
  `--sep`, or `--snapshot-lowe` on the collect stage.
- **The real test is downstream.** A VAE that trains beautifully but yields ~1% global-move
  acceptance in the sampling stage has not helped. Report the training numbers as
  provisional until a `deepthermo-wl --mode sample` run confirms them.

## Two couplings that fail silently

- **Grid size** is derived from the engine's padding rule (`VAE_D = 3N−2`, then padded
  with a *truncating* divide, so N=5 → 15, N=10 → 32), never from the coordinate range in
  the xyz file. The wrapper handles this; do not pass a hand-computed grid.
- **Element order** indexes the one-hot channel. A different order to the collect stage
  silently trains on permuted species and the model will be quietly wrong.

## Other alloys

The machinery is chemistry-agnostic — any number of species — but the configurations must
come from a `deepthermo-wl` collect run for the same system, and that run needs DFT pair
couplings for the alloy. Only **MoNbTaW** ships with fitted couplings. If asked for a
system with none available, say so plainly rather than reusing another alloy's.
