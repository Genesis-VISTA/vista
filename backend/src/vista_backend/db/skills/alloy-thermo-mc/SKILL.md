---
name: alloy-thermo-mc
description: >-
  Predict the order-disorder transition temperature (Tc) and short-range order of a
  refractory high-entropy alloy from its composition, via parallel-tempering (replica
  exchange) lattice Monte Carlo on a DFT-derived pair-interaction Hamiltonian, running
  on OLCF Odo or Frontier through VISTA's HPC backend. The flagship system is the
  4-element MoNbTaW BCC refractory HEA, which ships with fitted DFT couplings; the
  engine is chemistry-agnostic and handles any number of species on BCC/FCC/SC lattices
  given a matching coupling matrix. Use this skill whenever the user wants to compute or
  predict the transition/critical temperature Tc, specific heat, Warren-Cowley
  short-range-order parameters, susceptibility, or order-disorder behavior of a
  substitutional alloy or high-entropy alloy; screen alloy compositions for thermodynamic
  stability or ordering; or run lattice / cluster-expansion Monte Carlo — even if they
  don't explicitly say "Monte Carlo" or "parallel tempering". In a multi-agent HPC
  campaign this is the thermodynamics sim skill: one subagent per candidate composition.
metadata:
  version: "0.1.0"
  tags: ["Materials Design", "High Entropy Alloy Design", "Monte Carlo", "HPC", "OLCF"]
license: MIT
author: VISTA Team
---

# Alloy thermodynamics: Tc from composition by parallel-tempering Monte Carlo

This skill computes the finite-temperature thermodynamics of a substitutional alloy
whose energy is a **pair-interaction (cluster-expansion) Hamiltonian** parameterized by
DFT-derived effective pair interactions `J_ij`. Given a composition it returns the
**order-disorder transition temperature Tc**, the specific-heat and susceptibility
curves behind it, and Warren-Cowley short-range-order (SRO) parameters.

The flagship system is **MoNbTaW** (BCC refractory HEA), whose 6-shell DFT coupling
matrices ship with the engine repo. The machinery is chemistry-agnostic — any number of
species on BCC/FCC/SC — but **each alloy system needs its own `J_ij` couplings**; the
bundled ones are valid only for Mo/Nb/Ta/W.

## The code lives in a public repo — clone it, don't expect it vendored

The implementation (the C++17/MPI engine, the input/analysis scripts, the reference
docs, and the fitted MoNbTaW couplings) is **not vendored into VISTA**. It is a public
GitHub repo you clone at runtime:

> **`https://github.com/jqyin/alloy-thermo-skill`** (engine + scripts, small)

VISTA carries only this SKILL.md and a thin HPC job (`hpc_jobs/alloy-thermo-mc`) that
clones and builds the same repo on the compute node.

There are **two execution contexts**, and they bootstrap the code differently:

| Context | Where | What runs here | How the code arrives |
|---|---|---|---|
| **Sandbox (CPU)** | `run_bash` sandbox | inspect couplings, re-analyze CSVs, **plot** | you `curl`+`tar` the repo into `/mnt/data/output` |
| **HPC (MPI)** | OLCF Odo / Frontier | the **Monte Carlo run** | `submit_hpc_job` — the job clones and builds it for you |

## Bootstrapping the code in the sandbox (CPU steps only)

`git` is **not** installed in the sandbox, but `curl`, `python3`, and `pip` are, and the
sandbox has outbound network. Fetch the repo as a tarball once per session:

```bash
cd /mnt/data/output
curl -fsSL https://github.com/jqyin/alloy-thermo-skill/archive/refs/heads/main.tar.gz \
  | tar xz
cd alloy-thermo-skill-main
```

Do **not** try to build or run the engine in the sandbox — it is an MPI code and the
sandbox has one core and no MPI. Write all artifacts under `/mnt/data/output/...` and
print `Plot saved to <path>` for any image so the UI auto-displays it.

## The workflow

```
composition  →  [build inputs]  →  composition/coupling/control.input
control.input →  [run]          →  thermo_run<i>.csv     ← MPI / HPC
csv           →  [analyze]      →  Tc, SRO, figures
```

1. **Define the composition.** Atom fractions per element, **summing to exactly 1.0**
   (tolerance 1e-3). For MoNbTaW that is `[Mo, Nb, Ta, W]`; the equiatomic point is
   `0.25, 0.25, 0.25, 0.25`. One composition is one state point; a screen is a set of them.

2. **Run the Monte Carlo on HPC** — this is the MPI step. **Do not run it in the
   sandbox.** Submit it to Odo (or Frontier) via VISTA's HPC backend — see
   [Running the MC on HPC](#running-the-mc-on-hpc-the-mpi-step).

3. **Read the result.** The job writes `results.json` with `metrics.Tc_cv_K` (the
   specific-heat-peak estimate, the primary number) and `metrics.Tc_chi_K` (the
   susceptibility-peak cross-check), plus SRO and run-quality flags.

4. **Interpret** (see [Reading the result](#reading-the-result-honestly)). Always report
   Tc **with** whether the two estimators agree and whether the ladder bracketed the
   peak — a Tc from an unbracketed peak is not a measurement.

## Running the MC on HPC (the MPI step)

The run is wrapped as the VISTA HPC job **`alloy-thermo-mc`**. One submission = one
composition. The job clones the repo on the node, builds the engine (~1-2 min), runs the
parallel-tempering ladder, analyzes it, and writes `results.json` to the job output dir.

```python
submit_hpc_job(
    job="alloy-thermo-mc",
    cluster="odo",                       # or "frontier"
    script_args="--mo 0.30 --nb 0.25 --ta 0.25 --w 0.20",
)
```

**MPI ranks are temperature replicas.** The engine puts one replica of the ladder
(geometrically spaced between `T_init` and `T_final`) on each rank. The job defaults
allocate 2 nodes x 56 ranks = **112 replicas**.

### `script_args` contract

| Flag | Meaning | Default |
|---|---|---|
| `--mo F` / `--nb F` / `--ta F` / `--w F` | atom fractions; **must sum to 1.0 ± 1e-3** | required |
| `--n N` | linear lattice size; sites = N^3 | 12 |
| `--t-init T` / `--t-final T` | ladder endpoints in K | 200 / 2000 |
| `--n-runs K` | independent runs — **sequential per rank, multiplies walltime** | 2 |
| `--n-drop S` | equilibration sweeps | 5000 |
| `--n-samples S` | measurement samples | 20000 |
| `--walltime-hours H` | engine soft budget; checkpoints near 0.9x and exits | 0.07 |
| `--lattice L` | `bcc` \| `fcc` \| `sc` | `bcc` |
| `--seed N` | base RNG seed | 12345 |

Cost scales as `n_runs * (n_drop + n_samples) * N^3`. The defaults are **screening**
values sized for a ~5-minute run; the repo's own example spec carries production values
(N=16, n_runs=4, 100k sweeps) that take ~2 h. Ask for production fidelity explicitly.

### Outputs (in the job's `$VISTA_OUT`)

- `results.json` — the result (schema below).
- `summary.md` — human-readable analysis summary.
- `thermo.png` — energy, specific heat, susceptibility, Binder cumulant vs T.
- `order.png` — Warren-Cowley SRO parameters vs T.

Poll with `get_hpc_job_status(job_id, cluster="odo")`; when complete, fetch with
`get_hpc_job_outputs(job_id, files=["results.json"], cluster="odo")`.

## Sim-skill contract for multi-agent campaigns

In a VISTA campaign, the planner fans out **one thermodynamics subagent per candidate
composition**; each subagent uses this skill to launch and parse one job. The two
operations a subagent performs:

- **dispatch(order) → job:** turn the order (four atom fractions, plus optional sampling
  overrides) into the `script_args` string above, call
  `submit_hpc_job(job="alloy-thermo-mc", cluster="odo", script_args=…)`, and record the
  returned `job_id` + `cluster`. **Validate the simplex before submitting** — a bad sum
  is rejected by the wrapper, but a bounced submission still costs a round-trip.
- **collect(job) → result:** once complete, `get_hpc_job_outputs(..., files=["results.json"], cluster=...)`
  and parse it into a structured result:
  ```json
  {"Tc_cv_K": <float>, "Tc_chi_K": <float>,
   "cv_peak": <float>, "chi_peak": <float>,
   "sro_alpha1": <float>, "swap_accept_mean": <float>,
   "peak_bracketed": <bool>, "estimators_agree": <bool>}
  ```
  If `peak_bracketed` is false, flag the run as unusable for ranking rather than
  reporting a Tc pinned to a ladder endpoint.

Subagents do not talk to each other; the planner and durable campaign state coordinate
them. To screen a set of compositions the planner issues N orders and dispatches N jobs
in parallel, then hands the collected results to its scorer for ranking.

A campaign manifest binds this skill as, e.g.:

```yaml
subagents:
  - role: thermo
    skill: alloy-thermo-mc
    job: alloy-thermo-mc
    collect_files: [results.json]
    args:
      encoding: flags
      map: {mo: --mo, nb: --nb, ta: --ta, w: --w}
```

## Reading the result honestly

- **`Tc_cv_K` is the primary number**, but a single system size cannot pin Tc precisely
  — the specific-heat peak shifts and sharpens with `N`. For a rigorous Tc you need
  finite-size scaling (several `N`, or Binder-cumulant crossings). Say so when precision
  matters.
- **Cross-check the two estimators.** `estimators_agree` applies the engine's own 15%
  rule to `|Tc_cv - Tc_chi|`. When they disagree, report both and treat the midpoint as
  indicative only.
- **`peak_bracketed: false` means the ladder missed the transition** — widen or re-center
  `--t-init` / `--t-final` and rerun. Do not report the endpoint as a Tc.
- **`swap_accept_mean` should be roughly 0.2–0.4.** Much lower means the replica ladder
  is too coarse to equilibrate across the transition; add ranks or narrow the T range.
- **SRO tells you whether a transition is real.** `sro_alpha1` is the mean Warren-Cowley
  parameter at the lowest ladder temperature: large `|alpha|` means genuine ordering,
  `alpha → 0` means a random solid solution. A Cv bump with no SRO signal is not an
  order-disorder transition.

## Other alloy systems and the coupling requirement

The engine is chemistry-agnostic, but **running it needs `J_ij` couplings valid for that
alloy**, and only **MoNbTaW (Mo/Nb/Ta/W, BCC)** ships with a fitted set. For another
system you need DFT-derived effective pair interactions for its elements, supplied as a
spec with per-shell coupling matrices (see `references/file_formats.md` in the repo).

If asked to simulate an alloy with no available couplings, say so plainly — you can lay
out the campaign and the input structure, but the run is blocked on the DFT parameters.
**Never run one alloy through another's couplings**; results would be physically
meaningless.
