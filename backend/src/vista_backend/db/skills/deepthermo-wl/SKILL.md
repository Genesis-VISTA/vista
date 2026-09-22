---
name: deepthermo-wl
description: >-
  Run Wang-Landau Monte Carlo sampling of a high-entropy alloy on HPC using DeepThermo,
  with a trained variational autoencoder proposing global moves, and collect the lattice
  configurations that VAE is trained on. Use this when the user wants the density of
  states, free energy, entropy, or full-temperature thermodynamics of a substitutional
  alloy; wants Wang-Landau or flat-histogram sampling rather than a fixed-temperature
  Monte Carlo run; wants to generate or collect MC configurations or snapshots as VAE
  training data; or asks about accelerating alloy sampling with a machine-learned
  proposal distribution. The flagship system is MoNbTaW. Two modes: `collect` gathers
  training configurations, `sample` runs the production Wang-Landau simulation.
metadata:
  version: "0.1.0"
  tags: ["Materials Design", "High Entropy Alloy Design", "Monte Carlo", "Machine Learning", "HPC", "OLCF"]
license: MIT
author: VISTA Team
---

# DeepThermo: Wang-Landau sampling with a VAE global move

Distributed Monte Carlo for high-entropy alloys, where large configuration moves are
proposed by a pre-trained variational autoencoder (paper: Yin, Wang & Shankar,
*DeepThermo*, IPDPS 2023). Wang-Landau sampling yields the **density of states**, and
hence the full temperature dependence of the thermodynamics in a single run — unlike a
fixed-temperature Monte Carlo sweep.

## Two modes, one binary

| `--mode` | What it does | Produces |
|---|---|---|
| `collect` | PT warm-up with snapshot capture on, WL outer loop short-circuited | `snap_*.xyz` — VAE training configurations |
| `sample` | the production WL run with VAE global moves | density of states, `vae.dat` acceptance |

## The three-stage pipeline

```
deepthermo-wl --mode collect   →  configurations
vae-orderparam                 →  encoder/decoder.pt   (the `vae-orderparam` skill)
deepthermo-wl --mode sample    →  density of states
```

**These are three separate HPC jobs and they are strictly ordered.** Run one, wait for it
to finish, check its diagnostics, then run the next. Do not dispatch them together.

Pass the **same `--workspace NAME`** to all three in `script_args` (it is a flag, not a
setting — `submit_hpc_job` passes no environment).
The workspace is a persistent directory on shared storage holding
`collect/` snapshots, `train/` intermediates, and `models/` — it is how each stage's
output reaches the next. `results.json` reports the workspace path.

## The code lives in a public repo — cloned at runtime

The job clones **`https://github.com/jqyin/DeepThermo-WL`** (branch `vae-proposal`) with
its `vae-modeling` submodule and builds the engine on the node. The engine links LibTorch, which
comes from the `xforge` environment's PyTorch (the same one the `forge-tune` job uses,
on both Odo and Frontier) — nothing needs provisioning.

## Running it

```python
# stage 1 — gather configurations
submit_hpc_job(job="deepthermo-wl", cluster="odo",
               script_args="--mode collect --workspace tc-n10 --n 10 --samples 400 --sep 10")

# stage 3 — production WL, after vae-orderparam has trained a model.
# NO energy window: it is read from what stage 1 actually observed.
submit_hpc_job(job="deepthermo-wl", cluster="odo",
               script_args="--mode sample --workspace tc-n10 --n 10")
```

**MPI ranks are PT replicas** — one per GCD. Defaults allocate 2 nodes × 8 = 16 on
Frontier. See the job README for the full `script_args` table.

**Odo and Frontier only.** Both get PyTorch + ROCm from the same `xforge` environment the
`forge-tune` job uses. Perlmutter is not supported: its dispatcher runs the job script
once per rank inside `shifter`, which does not fit a wrapper that must run once and
launch the ladder itself. If asked for Perlmutter, say so rather than trying.

## Sim-skill contract

- **dispatch(order) → job:** turn (mode, lattice, composition, sampling knobs) into
  `script_args`, `submit_hpc_job(job="deepthermo-wl", …)`, record `job_id` + `cluster`.
- **collect(job) → result:** fetch `results.json`:
  ```json
  {"mode": "sample", "workspace": "<path>", "replicas": 16,
   "used_bootstrap_model": false,
   "vae_move": {"vae_attempts": 29146, "vae_accepts": 17275, "vae_acceptance": 0.593}}
  ```
  In `collect` mode the useful fields are `n_snapshot_files`, `used_bootstrap_model`, and
  `observed_energy`.

**Do not hand-copy an energy window out of `observed_energy`.** It reports the engine's
`pt_e_total_DO_NOT_USE_AS_WINDOW` (TOTAL energies, as printed) alongside `pt_e_per_site`
and a ready `suggested_window`. `[wang_landau] e_min/e_max` are **per site** — total / N³ —
so passing the totals gives a window ~N³ too wide that can never flatten, and the run
spins silently until walltime. Omit the flags; the sample stage reads the right window
itself. (It also validates them and refuses obviously-total values, but do not rely on
that.)

## The bootstrap model — expected, not an error

`collect` mode needs a **loadable model before one has been trained**: the engine's PT
loop computes its order parameter through `encode()`, so it loads an encoder even when
only gathering data. The job exports a random-weight model if none exists and reports
`used_bootstrap_model: true`. This is correct and expected — the configurations come from
Metropolis/PT physics and are unaffected by the weights; only the order-parameter column
is meaningless, and collection does not use it.

If `used_bootstrap_model` is **true on a `sample` run**, that is a real problem: the
sampler is proposing from random weights and acceptance will be ~1%. It means the
training stage did not write into this workspace.

## Each run starts clean

Every run clears its stage's previous output (`*.dat`, `snap_*.xyz`, checkpoints) before
starting, so mirrored progress files always belong to the current run and a stale
checkpoint cannot silently resume. `results.json` reports `cleared_stale_artifacts`. The
trained model and the other stages' directories are never touched. Pass `--keep-existing`
only if the user explicitly wants to accumulate across runs.

## Watching a long run — report progress, don't just wait

A WL run takes hours. The job mirrors its live diagnostics into the job output dir every
60 s, so fetch them **while the job is still running** and tell the user where it is,
rather than reporting nothing until the end:

```python
get_hpc_job_status(job_id, cluster="odo")          # lists what is available now
get_hpc_job_outputs(job_id, files=["vae.dat"], cluster="odo")
```

- **`DOS_H_iter<NNN>.dat`** — the density of states so far, one per WL iteration. The
  highest `NNN` present is the progress indicator: the schedule runs `mod_factor`
  1.0 → 1e-6 by halving, so roughly 20 iterations in total.
- **`vae.dat`** — cumulative VAE move attempts/accepts. Its ratio tells you early whether
  the surrogate is working; ~1% means an untrained model (see the bootstrap note).
- **`misc0.dat`** — PT swap acceptance, the thing to check if a run looks stalled.

Fetch only these **small diagnostics** mid-run. Snapshots are deliberately not in the
output dir, and asking for a file that does not exist yet makes the Globus transfer
retry rather than fail.

### You cannot fetch the workspace directly

`get_hpc_job_outputs` only reaches the job's own output dir. The workspace — snapshots,
models, the full DOS history — is a sibling directory and cannot be fetched. Use the
`workspace_inventory` in `results.json` to report what is there (`models_present`,
`encoders`, per-stage file counts) instead of trying to read the workspace path, and use
the mirrored diagnostics above for live values. Never tell the user to expect a workspace
file back from `get_hpc_job_outputs`.

## Reading a `sample` run

`vae_acceptance` is the number that matters. Upstream reference, identical config and
seed, varying only the model:

| | acceptance | sites changed per move |
|---|---|---|
| trained VAE | 59.3% | 2.1% |
| random weights | 0.9% | 74.8% |
| local `BondSwap` | — | 0.2% |

**High acceptance alone is not success.** If the decoder reconstructs its input almost
exactly, the "global" move changes almost nothing and is accepted trivially. A useful
move is ~10× larger than a local swap *and* usually accepted. `--z-r` (the latent step
radius) trades move size against acceptance. Acceptance also decays as the WL modification
factor shrinks — that is expected, not a regression.

## Known traps — check for these before blaming the physics

- **A WL window must be reachable.** The flatness loop cannot exit until every masked bin
  has been visited, so a window whose lower edge sits below the reachable ground state
  spins forever with **no warning**. You normally do not set it: the `collect` stage
  records this system's observed energies in the workspace, and `sample` reads the window
  from there. Pass `--e-min`/`--e-max` only to override, and **never copy values from
  another system or lattice size** — they are per-site energies specific to the couplings
  and N. If `sample` reports no window available, run `collect` on that workspace first.
- **PT may not mix on large lattices.** Swap acceptance goes as `exp(-Δβ·ΔE)` and ΔE is
  extensive, so a big lattice on few replicas may not exchange at all. Check `misc0.dat`
  after collecting; near-zero swap acceptance means more replicas, a much larger `--sep`,
  or `--snapshot-lowe`.
- **Element order and grid size** must match across all three stages — see the
  `vae-orderparam` skill. The wrappers derive the grid; do not hand-compute it.

## Relationship to `alloy-thermo-mc`

`alloy-thermo-mc` estimates Tc from a specific-heat peak in a parallel-tempering run —
cheap, one composition per job, and what the alloy Tc campaign uses. DeepThermo is the
heavier instrument: Wang-Landau gives the whole density of states, so Tc and the rest of
the thermodynamics follow from one run, at the cost of a trained VAE and a much longer
setup. Use `alloy-thermo-mc` for screening; use this when the density of states itself is
the goal.
