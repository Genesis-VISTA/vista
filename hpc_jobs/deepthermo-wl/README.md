# deepthermo-wl

Wang-Landau Monte Carlo for a high-entropy alloy, with a trained VAE proposing global
moves (DeepThermo, IPDPS 2023). One job = one stage of the engine.

Two modes, same binary, different `config.toml`:

| `--mode` | What it does | Produces |
|---|---|---|
| `collect` | PT warm-up with snapshot capture on, WL outer loop short-circuited | `snap_*.xyz` — the VAE's training configurations |
| `sample` | the real WL run, VAE global moves enabled | density of states, `vae.dat` acceptance stats |

Between them sits the [`vae-orderparam`](../vae-orderparam) job, which fits the VAE to
the collected configurations. The full pipeline and its diagnostics are documented
upstream in `docs/vae-workflow.md`.

## The workspace persists — that is the point

The three stages are **separate jobs**, so intermediate artifacts must outlive each one:
`collect`'s snapshots have to still be there when training starts, and the exported model
has to still be there when sampling starts. All of it lives in a named workspace on
shared storage:

```
<workspace>/
  collect/   config.toml, coupling.input, snap_*.xyz, misc0.dat
  train/     one-hot .npy, train/val splits, checkpoints/
  models/    encoder_<tag>.pt, decoder_<tag>.pt   <- what the sampler loads
  sample/    config.toml, vae.dat, DOS output
```

Pass the same `--workspace NAME` to all three jobs (it is a `script_args` flag, because
`submit_hpc_job` passes no environment). The workspace is **not** deleted on
exit and is **not** inside `$VISTA_OUT` — `get_hpc_job_status` recursively lists the
output dir over Globus, and a directory full of snapshots makes a status check crawl.
`$VISTA_OUT` gets `results.json`, `config.toml`, and small diagnostics only.

## Environment — the same `xforge` env forge-tune uses

```
module use /sw/aaims/crusher/modulefiles
module load xforge
```

That module ships PyTorch + ROCm, which covers **both** needs: the Python used to export
the bootstrap model, and LibTorch for the engine build. The LibTorch prefix is derived
from that PyTorch (`torch.utils.cmake_prefix_path`) — the same thing the repo's own
generic build instruction does — so **nothing has to be provisioned separately**.

Optional overrides: `DEEPTHERMO_TORCH_PREFIX` (a standalone LibTorch),
`DEEPTHERMO_PYTHON` (a different interpreter), `DEEPTHERMO_ENGINE_BIN` (a prebuilt
`hea-wl`, skipping the in-job CMake build).

On **Odo** the same environment is a shell script to source rather than a module:
`source /gpfs/wolf2/olcf/gen150/proj-shared/xforge/xforge-env.sh` (override with
`XFORGE_ENV`). Odo is MI250X/ROCm like Frontier, so the engine builds with
`-DDEEPTHERMO_PLATFORM=frontier` there too.

**Odo and Frontier only.** Both run the job script once on the head node, so the wrapper
can `srun` the ladder itself. Perlmutter's dispatcher runs it once *per rank* inside
`shifter`, which that design cannot support — adding it means restructuring, not copying
a file.

## `script_args` contract

| Flag | Meaning | Default |
|---|---|---|
| `--mode collect\|sample` | which stage | required |
| `--workspace NAME` | shared workspace; **use the same name for all three stages** | `default` |
| `--n N` | linear lattice size; sites = N³ | 10 |
| `--elements Mo,Nb,Ta,W` | species, **in the engine's `[lattice].elements` order** | MoNbTaW |
| `--composition 0.25,...` | atom fractions, must sum to 1.0 ± 1e-3 | equimolar |
| `--seed N` | RNG seed | 42 |
| `--z-r F` | latent step radius — trades VAE move size against acceptance | 0.1 |
| **collect** | | |
| `--samples K` | frames per rank | 400 |
| `--sep K` / `--drop K` | sweeps between samples / equilibration | 10 / 100 |
| `--t-init` / `--t-final` | PT ladder ends (K) | 10 / 2000 |
| `--snapshot-stride K` | append every kth PT sample | 1 |
| `--snapshot-lowe` | also snapshot first visits to the 10 lowest WL slots | off |
| **sample** | | |
| `--e-min` / `--e-max` / `--bin-width` | WL energy window | **derived from the collect stage** |
| `--flatness` | WL flatness criterion | 0.6 |
| `--mod-factor-init` / `--mod-factor-final` / `--iteration-factor` | WL schedule | 1.0 / 1e-6 / 2.0 |
| `--production-bin-samps K` | production samples per bin | 10 |

```python
submit_hpc_job(job="deepthermo-wl", cluster="odo",
               script_args="--mode collect --workspace tc-n10 --n 10 --samples 400 --sep 10")
```

**MPI ranks are PT replicas** — one per GCD. Defaults allocate 2 nodes × 8 = 16 ranks on
both Odo and Frontier (Slurm may give fewer; the wrapper reports what it got).

## Two couplings that fail silently

- **Grid size.** The engine pads an N³ lattice onto a cubic grid: `VAE_D = 3N−2`, then
  padded toward a multiple of 16 with a **truncating** divide — so it is *not* always a
  multiple of 16 (N=5 → 15, N=10 → 32). The wrapper derives it with the same arithmetic
  as the repo's `utils/geometry.py`. Never round up.
- **Element order.** The one-hot channel is the index into `[lattice].elements`. Passing
  `--elements` in a different order silently trains on permuted species.

## The bootstrap model

`collect` mode needs a **loadable model before one has been trained** — the engine's PT
loop computes its order parameter through `encode()`, so it loads the encoder even when
you are only gathering data. The wrapper exports a random-weight TorchScript pair if
`<workspace>/models` is empty. The configurations collected come from Metropolis/PT
physics and are unaffected by the random weights; only the order-parameter column is
meaningless, and collection does not use it. `results.json` reports
`used_bootstrap_model` so this is never ambiguous.

## Each run starts from a clean stage dir

The workspace persists and every run of a stage reuses the same `<workspace>/<mode>/`
directory, so the job **clears the previous run's output before starting**: all `*.dat`
(including `DOS_H_iter*`), `snap_*.xyz`, `engine.log`, and the `state*.input` /
`mc*.input` checkpoints. `results.json` lists what was removed in
`cleared_stale_artifacts`.

Two reasons this is not optional:

- **Progress would lie.** The first mirror pass runs immediately, so without clearing it
  copies the *last* run's `DOS_H_iter*.dat` into `$VISTA_OUT` and they read as this run's
  progress.
- **A stale checkpoint could resume the wrong run.** `state<N>.input` is a restart file;
  leaving one lets the engine pick up a configuration that no longer matches `config.toml`.

What is **never** touched: the `models` symlink and the trained VAE behind it,
`<workspace>/train/`, and the other stages' directories. `--keep-existing` disables the
clearing if you really want to accumulate.

## Watching a run in progress

WL runs take hours, so the job **mirrors its live diagnostics into `$VISTA_OUT` while it
runs** (every `--progress-interval` seconds, default 60):

| File | What it tells you |
|---|---|
| `DOS_H_iter<NNN>.dat` | the density of states so far — one per WL iteration, rewritten every 100 sweeps as that iteration proceeds |
| `vae.dat` | cumulative VAE global-move attempts / accepts / ratio |
| `misc0.dat` | PT swap acceptance (check this if the run looks stuck) |
| `engine.log` | full engine stdout |

So `get_hpc_job_status` lists them and `get_hpc_job_outputs` can fetch them **before the
job finishes** — the iteration number in the filenames is the progress bar. With
`mod_factor` going 1.0 → 1e-6 by halving there are only ~20 iterations, so
`DOS_H_iter007.dat` means roughly a third of the way through the schedule.

The mirror is deliberately **bounded** (64 files, 4 MB each) and never includes
snapshots: `get_hpc_job_status` recursively lists the output dir over Globus, and an
unbounded mirror would make a status check crawl. `--progress-interval 0` disables it.

### The workspace itself cannot be fetched

`get_hpc_job_outputs` rejects `..` and absolute paths, so it can only reach
`$VISTA_OUT/<job_id>/`. The workspace is a **sibling** of that directory and is
structurally unreachable from outside the job — the mirrored diagnostics above are the
only view into it while a job runs.

For everything else, `results.json` carries a `workspace_inventory`: per-stage file
counts, byte totals, a sample of names, and `models_present` / `encoders` /
`bootstrap_marker_present`. That answers "did collect produce snapshots?" and "is there a
trained model or just the bootstrap?" without transferring anything. To pull a workspace
file that is not mirrored, add it to `PROGRESS_GLOBS` in `run_stage.py` (keeping the
listing cheap) or copy it in a follow-up job.

## Reading `sample` results

`results.json` carries `vae_move` from the last row of `vae.dat`: cumulative attempts,
accepts, and the acceptance ratio. Upstream's reference numbers on an identical config,
varying only the model:

| | acceptance | sites changed per move |
|---|---|---|
| trained VAE | 59.3% | 2.1% |
| random weights | 0.9% | 74.8% |
| local `BondSwap` | — | 0.2% |

**High acceptance alone is not success.** If the decoder reconstructs its input almost
exactly, the "global" move barely changes anything and is accepted trivially — check the
move size too. A sub-1% acceptance usually means the model is untrained (still the
bootstrap) rather than that the physics is wrong.

## Known traps

- **A WL window must be reachable.** `flatWL()` cannot exit until every masked bin has
  been visited, so a window whose lower edge sits below the reachable ground state spins
  forever with no warning — it just burns walltime.

  The job handles this rather than leaving it to you: the `collect` stage parses the
  engine's `ptEmin`/`ptEmax` line, converts to per-site units, insets 5% at each end, and
  writes `<workspace>/energy_window.json`. The `sample` stage reads that when
  `--e-min`/`--e-max`/`--bin-width` are not given, and **fails at submit time** if neither
  is available. There are deliberately no built-in defaults: the repo's example values
  (`-1.2808`/`-1.2770`) are MoNbTaW at N=10 with those particular DFT couplings and mean
  nothing for another system or size.

  **`bin_width` is in TOTAL energy units, not per-site.** The engine computes
  `bins = (e_max - e_min) / (bin_width / N³)`, so the per-site bin is `bin_width / N³`.
  Deriving it per-site instead is wrong by a factor of N³ and silently yields a histogram
  that can never flatten.
- **PT may not mix on large lattices.** Swap acceptance goes as `exp(-Δβ·ΔE)` and ΔE is
  extensive, so a big lattice on few replicas may not exchange at all — check
  `misc0.dat`. Upstream saw zero swaps above 207 K at N=10 on 8 ranks, which made the
  low-energy configurations the thinnest part of the training set.
