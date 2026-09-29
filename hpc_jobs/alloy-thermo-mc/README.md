# alloy-thermo-mc

Parallel-tempering Monte Carlo thermodynamics for one refractory high-entropy alloy
composition. Returns the order-disorder transition temperature **Tc** (from the
specific-heat peak, cross-checked against the susceptibility peak), plus the
Warren-Cowley short-range-order parameter and run-quality signals.

One submission = **one composition** = one Tc. That is the subagent unit a campaign
planner fans out over.

## What runs

The simulation code is **not vendored here**. The job clones the public
[`alloy-thermo-skill`](https://github.com/jqyin/alloy-thermo-skill) repo on the compute
node and builds its C++17/MPI engine there (~1-2 min; no dependencies beyond MPI, so a
per-job build is cheaper than maintaining a pre-provisioned environment).

```
composition  -> spec.json        (DFT pair couplings reused from the repo's MoNbTaW example)
spec.json    -> make_inputs.py   -> composition/coupling/control.input
control.input-> srun alloy_mc    -> thermo_run<i>.csv        <- the parallel step
csv          -> analyze.py       -> summary.json
summary.json -> results.json     (campaign-facing metrics)
```

**MPI ranks are temperature replicas.** The engine places one replica of the
parallel-tempering ladder on each rank, geometrically spaced between `T_init` and
`T_final`. The defaults allocate 2 nodes x 56 ranks = **112 replicas**.

### This job spans multiple nodes — the build must be on shared storage

`srun` launches ranks across **every** node in the allocation, and each rank both
`execve`s the binary *and* reads `control.input` / `composition.input` /
`coupling.input` from the run directory. (Only rank 0 writes `thermo_run<i>.csv`, but
all ranks read the inputs.) Building into node-local `/tmp` therefore works on one node
and fails the instant the allocation spans two:

```
error: execve(): /tmp/alloymc-44384/alloy-thermo-skill/engine/alloy_mc: No such file or directory
```

So the clone, the build, and the run directory go on **shared scratch**: a sibling of
`$VISTA_OUT` (`ALLOYMC_SCRATCH_DIR` overrides it), removed when the job exits. They
cannot go *inside* `$VISTA_OUT` — `get_hpc_job_status` recursively lists that directory
over Globus, and a build tree plus a `.git` dir makes a status check hang for minutes.
Only the Python venv and the pip/matplotlib caches stay node-local, since just the batch
node needs them and a venv install on a parallel filesystem is slow.

The job verifies the binary is visible from every node before launching the ladder, so a
misconfigured `ALLOYMC_SCRATCH_DIR` fails fast with a clear message instead of an
`execve()` error partway in.

## Driving it from the API

[`docs/api-example-alloy-tc.md`](../../docs/api-example-alloy-tc.md) walks through
estimating Tc end to end through the vista API, with a runnable script at
[`backend/scripts/example_alloy_tc.py`](../../backend/scripts/example_alloy_tc.py).

## `script_args` contract

Pass one composition, plus optional sampling overrides, as a single flat string:

| Flag | Meaning | Default |
|---|---|---|
| `--mo F` | Mo atom fraction | required |
| `--nb F` | Nb atom fraction | required |
| `--ta F` | Ta atom fraction | required |
| `--w F` | W atom fraction | required |
| `--n N` | linear lattice size; sites = N^3 | 12 |
| `--t-init T` / `--t-final T` | ladder endpoints (K) | 200 / 2000 |
| `--n-runs K` | independent runs — **sequential per rank, multiplies walltime** | 2 |
| `--n-drop S` | equilibration sweeps | 5000 |
| `--n-samples S` | measurement samples | 20000 |
| `--walltime-hours H` | engine soft budget; checkpoints near 0.9x and exits | 0.07 |
| `--lattice L` | `bcc` \| `fcc` \| `sc` | from the base spec (`bcc`) |
| `--seed N` | base RNG seed | 12345 |

**Mo + Nb + Ta + W must equal 1.0 (+/- 1e-3).** These are atom fractions; the wrapper
rejects anything else before launching. The engine itself only warns, so this gate is
the one that matters.

```python
submit_hpc_job(
    job="alloy-thermo-mc",
    cluster="odo",
    script_args="--mo 0.30 --nb 0.25 --ta 0.25 --w 0.20",
)
```

## Sampling defaults are cheap on purpose

Cost scales as `n_runs * (n_drop + n_samples) * N^3`. The repo's own
`examples/MoNbTaW/spec.json` carries **production** values (N=16, n_runs=4, 100k
sweeps) that take ~2 h. This wrapper overrides them with screening values sized for
the campaign's ~5-minute budget, so a bare submission cannot burn a 2-hour allocation
by accident. Ask for production fidelity explicitly.

> **These defaults are an estimate, not a measurement.** Whether 112 replicas
> equilibrate at N=12 within 5 minutes has not been verified on Odo. Treat the first
> submission as a **calibration run**: check the reported walltime and
> `swap_accept_mean`, then retune before dispatching a full campaign cycle.

## Outputs (in `$VISTA_OUT`)

- `results.json` — the campaign-facing result (see below).
- `summary.md` — the human-readable analysis summary.
- `thermo.png` — energy, specific heat, susceptibility, Binder cumulant vs T.
- `order.png` — Warren-Cowley SRO parameters vs T.

Everything else (the clone, the build tree, the venv, `thermo_run*.csv`, checkpoints)
lives in scratch and is discarded when the job exits — see
[the shared-storage note](#this-job-spans-multiple-nodes--the-build-must-be-on-shared-storage).
Keeping `$VISTA_OUT` small is what keeps `get_hpc_job_status`' recursive Globus listing fast.

### `results.json`

```json
{"job": "alloy-thermo-mc",
 "composition": {"Mo": 0.30, "Nb": 0.25, "Ta": 0.25, "W": 0.20},
 "metrics": {
   "Tc_cv_K": 1180.0, "Tc_chi_K": 1240.0,
   "cv_peak": 0.42, "chi_peak": 3.1,
   "sro_alpha1": -0.31, "swap_accept_mean": 0.28,
   "peak_bracketed": true, "estimators_agree": true},
 "run": {"lattice": "bcc", "replicas": 112, "seed": 12345, "...": "..."}}
```

`Tc_cv_K` is the primary metric. The rest are the campaign scorer's inputs:
`sro_alpha1` (|alpha| at the lowest ladder temperature) distinguishes genuine
ordering from a spurious Cv bump, while `peak_bracketed`, `estimators_agree` and
`swap_accept_mean` are run-quality signals reported as advisory.

## Clusters

`odo` and `frontier`, both 2 nodes x 56 ranks, 600 s walltime. Odo is the primary
target; the job is CPU-only, so it uses a Frontier node's CPU cores and leaves its
GPUs idle — prefer Odo unless Frontier is what you have.

`ALLOYMC_SCRATCH_DIR` overrides the shared scratch location on either cluster; leave it
unset to use the `$VISTA_OUT` sibling, which is writable on both (Odo's output dir is
pre-created group-writable; Frontier's is granted to the IRI automation user by setfacl).

A `setup_<cluster>.sh` pre-launch validation gate is not shipped yet; the clone, build,
and cross-node visibility checks fail loudly inside the job instead.
