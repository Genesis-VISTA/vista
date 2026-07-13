---
name: salt-chemistry-md
description: >-
  Predict chemistry properties of molten salts — primarily mass density — as a
  function of composition and temperature, via OpenMM NPT molecular dynamics driven
  by a MACE machine-learning potential, running on OLCF Frontier GPUs through VISTA's
  HPC backend. The flagship system is Flibe (LiF-BeF2), the fusion breeder/coolant
  salt, which ships with a fitted potential; the engine is chemistry-agnostic and also
  handles FLiNaK, chloride salts, and arbitrary mixtures given a matching potential.
  Use this skill whenever the user wants to compute or predict the density (or other
  equilibrium liquid properties) of a molten salt, Flibe, FLiNaK, a fluoride/chloride
  melt, or a fusion breeder-blanket / tritium-breeding salt; build a salt simulation
  box from a composition (mol% of each component); run NPT MD with a MACE or
  machine-learning interatomic potential; or screen candidate salt compositions and
  temperatures and plot density vs temperature or vs composition — even if they don't
  explicitly say "OpenMM" or "MACE". In a multi-agent HPC campaign this is the
  chemistry/density sim skill: one subagent per (composition, temperature) state point.
metadata:
  version: "0.1.0"
  tags: ["Materials Design", "Molten Salt Tritium Breeding", "Molecular Dynamics", "HPC"]
license: MIT
author: SPLASH team
---

# Molten-Salt MD: density from composition and temperature

This skill predicts equilibrium properties (primarily **mass density**) of molten salts
using **NPT molecular dynamics** in **OpenMM**, driven by a **MACE** machine-learning
interatomic potential trained on DFT data. It is built to be driven end-to-end by an
agent: take a candidate salt (composition) and conditions (temperature), run on HPC
GPUs, and return density with honest error bars and a plot.

The flagship system is **Flibe** (LiF–BeF₂), the fusion breeder/coolant salt, which
ships with a fitted potential (`assets/mace_flibe.model`). The composition,
structure-building, analysis, and HPC machinery are **chemistry-agnostic** — FLiNaK,
chloride salts, and arbitrary mixtures work too — but **each salt family needs its own
ML potential**: the bundled model is valid only for Li/Be/F. See
[Other salts](#other-salts-and-the-potential-requirement).

## The code lives in a public repo — clone it, don't expect it vendored

The implementation (the `saltmd` package, the CLI scripts, the reference docs, and the
fitted Flibe potential) is **not vendored into VISTA**. It is a public GitHub repo that
you clone at runtime:

> **`https://github.com/jqyin/salt-chemistry-skill`**  (`saltmd` package, ~10 MB)

This keeps the skill lightweight: VISTA carries only this SKILL.md and a thin HPC job
(`hpc_jobs/salt-chemistry-md`) that bootstraps the same repo on the compute node.

There are **two execution contexts**, and they bootstrap the code differently:

| Context | Where | What runs here | How the code arrives |
|---|---|---|---|
| **Sandbox (CPU)** | `run_bash` sandbox | build structures, analyze logs, **plot** results | you `curl`+`tar` the repo into `/mnt/data/output` |
| **HPC (GPU)** | OLCF Frontier | the **NPT MD run** (the only GPU step) | `submit_hpc_job` — the job clones the repo for you |

## Bootstrapping the code in the sandbox (CPU steps only)

`git` is **not** installed in the sandbox, but `curl`, `python3`, and `pip` are, and the
sandbox has outbound network. Fetch the repo as a tarball once per session:

```bash
cd /mnt/data/output
curl -fsSL https://github.com/jqyin/salt-chemistry-skill/archive/refs/heads/main.tar.gz \
  | tar xz
cd salt-chemistry-skill-main
python3 -m pip install --quiet pyyaml   # numpy/pandas/matplotlib are already present
```

Everything below that does **not** say "GPU" runs here, from this directory. Write all
artifacts under `/mnt/data/output/...` and print `Plot saved to <path>` for any image so
the UI auto-displays it.

## The workflow

```
composition + T  →  [build]  →  structure.pdb
structure.pdb    →  [run]    →  results.json  (density + provenance)   ← GPU / HPC
results.json...  →  [plot]   →  density.png + summary.csv
```

1. **Define the state point(s).** Composition is the **mol% of each component**. For
   Flibe that is mol% BeF₂ (33.33 = the 2LiF·BeF₂ eutectic). Temperature in kelvin (a
   typical Flibe coolant condition is ~783 K = 510 °C). One salt at one temperature is
   one state point; a screen is a grid of them.

2. **Build the structure** (CPU, in the sandbox) — generate a periodic box:
   ```bash
   python3 scripts/build_structure.py --mol-percent-bef2 33.33 \
       --n-formula-units 720 --seed 1 -o /mnt/data/output/structure.pdb
   # named preset:    --salt flinak -o flinak.pdb     (run --list-salts to see them)
   # explicit mix:    --components "NaCl:58,MgCl2:42" --density 1.6 -o nacl.pdb
   ```
   It prints a JSON summary (achieved mol%, element counts, box size, closest-pair
   distance). Heed a closest-pair warning — too-close ions blow up the ML forces at
   step 0.

3. **Run the NPT simulation on a GPU** — this is the GPU/HPC step. **Do not run it in
   the sandbox** (no GPU, 1 GB RAM). Submit it to Frontier via VISTA's HPC backend — see
   [Running the MD on Frontier](#running-the-md-on-frontier-the-gpu-step). The run does
   minimize → equilibrate → produce and writes `results.json`.

4. **Read the result.** `run_npt.py` writes the equilibrium density into `results.json`
   (`density.density_g_cm3` ± `density.density_stderr_g_cm3`). To re-analyze with a
   different equilibration cutoff, run `analyze_density.py` in the sandbox.

5. **Interpret / plot** (CPU, in the sandbox). Pull the `results.json` files back from
   the job with `get_hpc_job_outputs`, then:
   ```bash
   MPLBACKEND=Agg python3 scripts/plot_results.py /mnt/data/output/runs/*/results.json \
       -o /mnt/data/output/density.png --csv /mnt/data/output/summary.csv
   ```
   The x-axis auto-detects (T if temperature varies, composition if mol% varies). Always
   report the value **with its error bar** and the conditions it belongs to.

## Running the MD on Frontier (the GPU step)

The GPU run is wrapped as the VISTA HPC job **`salt-chemistry-md`**. One submission = one
state point = one GPU. The job clones the repo on Frontier, activates a pre-provisioned
OpenMM+MACE ROCm conda env, builds the structure, runs NPT, and writes `results.json`
into the job's output dir.

```python
submit_hpc_job(
    job="salt-chemistry-md",
    cluster="frontier",
    duration="2:00:00",                 # raise for long production runs
    script_args="--mol-percent-bef2 33.33 --temperature 783.15 "
                "--n-formula-units 720 --production-steps 250000",
)
```

### `script_args` contract

Pass one state point's build + run options as a single flat string:

| Flag | Meaning | Default |
|---|---|---|
| **Composition (pick one):** | | |
| `--mol-percent-bef2 P` | Flibe shortcut (mol% BeF₂) | — |
| `--salt NAME` | named preset (`flibe`, `flinak`, `licl-kcl`, …) | — |
| `--components "F:p,F:p"` | explicit mixture (formula:mol%) | — |
| `--temperature T` | temperature in K | required |
| `--n-formula-units N` | system size | preset default |
| `--production-steps S` | production MD steps | preset default |
| `--model PATH` | ML potential; relative paths resolve in the clone | `assets/mace_flibe.model` (Flibe only) |
| `--density D` | initial box density (g/cm³) for explicit `--components` | — |
| `--seed N` | RNG seed for box packing | 1 |

For any **non-Flibe** salt you **must** pass `--model` pointing at a potential trained on
that chemistry (see below); the default Flibe model is only valid for Li/Be/F.

### Outputs (in the job's `$VISTA_OUT`)

- `results.json` — the result. Density at `density.density_g_cm3` ±
  `density.density_stderr_g_cm3`, plus the achieved composition, temperature, box, and
  run provenance. (See `reference/outputs.md` in the repo for the full schema.)
- `structure.pdb` — the periodic box that was simulated.
- `equilibration.csv` / production logs — for checking convergence.

Poll with `get_hpc_job_status`; when complete, fetch with
`get_hpc_job_outputs(job_id, files=["results.json"], cluster="frontier")`.

## Sim-skill contract for multi-agent campaigns

In a VISTA campaign, the planner fans out **one chemistry subagent per candidate state
point**; each subagent uses this skill to launch and parse one Frontier job. The two
operations a subagent performs:

- **dispatch(order) → job:** turn the order (a composition + temperature, plus optional
  size/steps/model) into the `script_args` string above, call
  `submit_hpc_job(job="salt-chemistry-md", cluster="frontier", duration=…, script_args=…)`,
  and record the returned `job_id` + `cluster`.
- **collect(job) → result:** once the job completes, `get_hpc_job_outputs(... files=["results.json"])`
  and parse it into a structured result:
  ```json
  {"density_g_cm3": <float>, "density_stderr_g_cm3": <float>,
   "temperature_K": <float>, "composition_mol_pct": {...},
   "n_atoms": <int>, "converged": <bool>}
  ```
  If `equilibration` looks too short (density still drifting), flag it as not converged
  rather than reporting a biased mean.

Subagents do not talk to each other; the planner and durable campaign state coordinate
them. To screen a grid, the planner issues N orders (one per (composition, T)) and
dispatches N jobs in parallel — Frontier runs one GPU per state point — then collects and
hands the results to the planner's scorer for ranking/plotting.

## Other salts and the potential requirement

The code is chemistry-agnostic, but **running MD needs an ML potential valid for that
chemistry**, and only **Flibe (Li/Be/F)** ships with one. For another salt:

1. Obtain/train a MACE (or other OpenMM-ML-compatible) model for its elements.
2. Pass it with `--model /path/to/model` in `script_args`.

If asked to simulate a chemistry with no available model, say so plainly — you can still
build the structure and lay out the campaign, but the run is blocked on the potential.
**Never run a salt through the Flibe model**; results would be physically meaningless.

## Reference material (in the cloned repo)

- `reference/methodology.md` — the NPT density protocol, error bars, convergence. **Read
  this before judging whether a result is trustworthy.**
- `reference/hpc_frontier.md` — OLCF Frontier modules, conda env, SLURM, troubleshooting.
- `reference/outputs.md` — exact `results.json` schema.

## Guardrails

- Report density **with uncertainty**; a single number with no error bar is incomplete.
- Composition is given in **mol%**; integer atom counts round it slightly — record the
  **achieved** mol% from the output, not the requested one.
- **Match the potential to the chemistry.** Sanity-check against experiment where possible
  — eutectic Flibe is ~1.94 g/cm³ near 783 K.
- The GPU run is the only HPC step; in a campaign, never launch jobs without an approved
  plan, and cite the `job_id`/`results.json` for every number you report.
