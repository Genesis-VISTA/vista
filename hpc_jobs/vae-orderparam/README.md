# vae-orderparam

Fits a variational autoencoder to Monte Carlo configurations of a high-entropy alloy and
exports it as TorchScript. The trained encoder is two things at once:

- a **learned order parameter** — the latent coordinates of a configuration, which
  separate ordered from disordered states without hand-picking a symmetry to measure; and
- the **surrogate** that DeepThermo's Wang-Landau loop uses to propose global moves.

This is the middle stage of a three-stage pipeline. [`deepthermo-wl`](../deepthermo-wl)
runs the two ends:

```
deepthermo-wl --mode collect   ->  snap_*.xyz
vae-orderparam                 ->  encoder_<tag>.pt / decoder_<tag>.pt
deepthermo-wl --mode sample    ->  WL density of states, vae.dat acceptance
```

All three share one **persistent workspace** — pass the same `--workspace NAME` (a
`script_args` flag, since `submit_hpc_job` passes no environment). This job
reads `<workspace>/collect/snap_*.xyz` and writes `<workspace>/models/`, which is exactly
where the sampling stage looks. It fails immediately with a clear message if the
workspace or its snapshots are missing.

## What it runs

Steps 2–5 of the upstream `docs/vae-workflow.md`, plus an optional step 7 check:

1. `preprocessing/create_vae_input.py` — xyz → one-hot `.npy`
2. **dedupe + stratified split** (see below)
3. `vae/train_vae.py` — the VAE
4. `vae.src.export_torchscript` — `encoder_<tag>.pt` / `decoder_<tag>.pt`
5. `orderparameter/op_infer.py` — optional, with `--order-parameter`

### Why dedupe and stratify

Cold PT replicas repeat configurations because their dynamics freeze, so a naive random
split lets validation score frames the model memorised. The wrapper deduplicates first,
then splits **stratified by rank** so validation spans the whole temperature ladder
instead of landing entirely in the hottest replica. `results.json` reports
`duplicate_fraction` — it is a diagnostic in its own right. Upstream saw 2302/2800 unique
at N=10, with the duplicates concentrated at the cold end, which meant the low-energy
configurations were the *thinnest* part of the training set.

## Environment — the same `xforge` env forge-tune uses

```
module use /sw/aaims/crusher/modulefiles
module load xforge
```

PyTorch + numpy come from that module; nothing needs provisioning. Override the
interpreter with `DEEPTHERMO_PYTHON` if you need a different one.

On **Odo**, source `/gpfs/wolf2/olcf/gen150/proj-shared/xforge/xforge-env.sh` instead
(`XFORGE_ENV` overrides it) — the job handles this.

**Odo and Frontier only** — see the [`deepthermo-wl` README](../deepthermo-wl/README.md)
for why Perlmutter needs restructuring rather than a copied script.

## Each run starts clean

Previous training intermediates (`*.npy` splits, `checkpoints/*.pt`) are cleared before
training. Without that, a run failing mid-training would leave the export step to pick up
the *previous* run's weights and write them out as if freshly trained — and the sampling
stage would use a model that does not correspond to this run's data. `--keep-existing`
disables it. The exported model in `<workspace>/models/` is replaced only on success.

## `script_args` contract

| Flag | Meaning | Default |
|---|---|---|
| `--workspace NAME` | the workspace the collect stage wrote to | `default` |
| `--n N` | lattice N the snapshots came from | 10 |
| `--elements Mo,Nb,Ta,W` | **must match the engine's `[lattice].elements` order** | MoNbTaW |
| `--alloy-tag TAG` | names the exported files (`encoder_<TAG>.pt`) | MoNbTaW |
| `--skip-frames K` | drop the first K frames per file (unequilibrated) | 50 |
| `--n-ranks K` | PT ranks that produced the snapshots; stratifies the split | 8 |
| `--val-fraction F` | validation share per rank block | 0.1 |
| `--epochs K` | training epochs | 100 |
| `--batch-size K` / `--lr F` / `--seed K` | training knobs | 32 / 5e-4 / 6 |
| `--latent-dim K` | latent dimension — the order parameter's dimensionality | 3 |
| `--order-parameter` | also run `op_infer.py` over the frames | off |

```python
submit_hpc_job(job="vae-orderparam", cluster="odo",
               script_args="--workspace tc-n10 --n 10 --epochs 100 --order-parameter")
```

## Two couplings that fail silently

- **Grid size** is derived here from the engine's padding rule (`VAE_D = 3N−2`, then
  padded with a *truncating* divide — N=5 gives 15, not 16), using the same arithmetic as
  the repo's `utils/geometry.py`. It is **not** taken from the coordinate range in the xyz
  file, which gives the unpadded span and produces tensors the engine cannot load.
- **Element order** indexes the one-hot channel. A different order to `--elements`
  silently trains the model on permuted species. Pass the same order the engine config used.

## Reading the result

`results.json` carries:

- `data.frames_total` / `frames_unique` / `duplicate_fraction` — training-set health.
- `training.final` and `training.train_val_gap` — **watch the gap, not the absolute loss.**
  The epoch-1 training figure is an initialisation transient (sum-reduced BCE over
  `grid³ × n_elements` voxels plus an early KL spike) and recovers within one epoch.
  Upstream's gap closed to 1.2%, i.e. no overfitting.
- `encoder` / `decoder` — paths the sampling stage will load.

A cheap offline check before spending a WL run: reconstruction accuracy the way the
engine's `decode` uses the model (argmax per site, then the composition repair loop).
Upstream saw 93.6% trained vs 25.0% random — exactly chance for four species.
