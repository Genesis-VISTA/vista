---
name: salt-neutronics-tbr
description: >-
  Emulate a Shift Monte Carlo neutronics simulation of a FLiBe (LiF–BeF2)
  molten-salt tritium-breeding blanket WITHOUT running neutron transport on HPC.
  Given a salt composition (mol% BeF2) and a Li-6 enrichment, it interpolates a
  precomputed Shift parameter study to return the tritium breeding ratio (TBR), and
  can also return neutron flux or nuclide number densities, sweep over compositions,
  and render plots — in milliseconds, on a single CPU core. Use this skill whenever
  the user asks about tritium breeding ratio or TBR, FLiBe / LiF-BeF2 salt
  composition, mol% of BeF2, Li-6 (lithium-6) enrichment, the beryllium multiplier,
  neutron flux or isotopic/number densities in a breeding blanket, or wants to mock,
  emulate, predict, or interpolate what a Shift (or any Monte Carlo) neutronics run on
  HPC (e.g. OLCF) would produce for a given blanket composition — even if they do not
  name the tool. Also trigger it for sweeping TBR across composition or plotting TBR /
  flux / density surfaces. In a multi-agent HPC campaign this is the neutronics/TBR
  sim skill: one subagent per (composition, Li-6 enrichment) state point, and the
  natural companion to the salt-chemistry-md density sim for screening breeder salts.
metadata:
  version: "0.1.0"
  tags: ["Materials Design", "Molten Salt Tritium Breeding", "Neutronics", "HPC"]
license: MIT
author: SPLASH team
---

# Salt Neutronics TBR: tritium breeding ratio from composition and Li-6 enrichment

This skill answers "what tritium breeding ratio (TBR) would a Shift Monte Carlo
neutronics run have produced for *this* FLiBe blanket composition?" — not by running
neutron transport (which costs hours on HPC), but by **interpolating a precomputed
Shift parameter study**. The headline output is the **TBR** (tritium atoms bred per
source neutron); a blanket needs TBR ≳ 1.0 (with margin for losses) to be tritium
self-sufficient. It also returns neutron flux, nuclide number densities, composition
sweeps, and plots. The whole calculation is **CPU-only and sub-second**.

It is built to be driven end-to-end by an agent: take a candidate salt (mol% BeF₂ +
Li-6 enrichment), return TBR with an honest interpolated-vs-extrapolated flag and a
plot. In a VISTA campaign it is the **neutronics sim skill** — the companion to
[`salt-chemistry-md`](../salt-chemistry-md/SKILL.md) (which gives density): together
they let a planner screen breeder-salt compositions on both physics axes.

## The code lives in a public repo — clone it, don't expect it vendored

The implementation (the `salt_neutronics` package, the CLI, the reference docs, and the
bundled Shift parameter study `data/neutronics_isotopics.h5`, ~59 KB) is **not vendored
into VISTA**. It is a public GitHub repo that you clone at runtime:

> **`https://github.com/jqyin/salt-neutronics-skill`**  (`salt_neutronics` package, ~1 MB)

This keeps the skill lightweight: VISTA carries only this SKILL.md and a thin HPC job
(`hpc_jobs/salt-neutronics-tbr`) that bootstraps the same repo on the compute node.

There are **two execution contexts**. Unlike a GPU MD run, this calculation is cheap
enough to run in either — pick by what you're doing:

| Context | Where | Use it for | How the code arrives |
|---|---|---|---|
| **Sandbox (CPU)** | `run_bash` sandbox | ad-hoc single queries, sweeps, **plotting**, exploration | you `curl`+`tar` the repo into `/mnt/data/output` |
| **HPC (CPU)** | OLCF Odo (open enclave) **or** NERSC Perlmutter | the **per-state-point sim in a campaign** (`submit_hpc_job`) | the job `git clone`s the repo for you |

The HPC path is not required for correctness (there's no GPU step), but it is how a
**multi-agent campaign** runs one state point per subagent through VISTA's HPC backend,
symmetrically with the other sim skills — see
[Sim-skill contract](#sim-skill-contract-for-multi-agent-campaigns). For a one-off
question, just run it in the sandbox.

## Bootstrapping the code in the sandbox (CPU steps)

`git` is **not** installed in the sandbox, but `curl`, `python3`, and `pip` are, and the
sandbox has outbound network. Fetch the repo as a tarball once per session:

```bash
cd /mnt/data/output
curl -fsSL https://github.com/jqyin/salt-neutronics-skill/archive/refs/heads/main.tar.gz \
  | tar xz
cd salt-neutronics-skill-main
python3 -m pip install --quiet scipy h5py    # numpy/matplotlib are already present
export PYTHONPATH="$PWD:${PYTHONPATH:-}"      # package is importable in place, no install needed
export MPLBACKEND=Agg                         # headless plotting
```

Everything below runs here, from this directory. Write all artifacts under
`/mnt/data/output/...` and print `Plot saved to <path>` for any image so the UI
auto-displays it.

## The workflow

```
composition + Li-6  →  [tbr]    →  TBR + provenance        (results.json)
composition + Li-6  →  [flux]   →  neutron flux at a position
composition range   →  [sweep]  →  CSV (+ TBR surface PNG)
```

1. **Define the state point(s).** Composition is the **mol% BeF₂** (33.33 = the
   2LiF·BeF₂ eutectic). Li-6 enrichment is the ⁶Li atom fraction (default 0.075 ≈
   natural). One composition + one enrichment is one state point; a screen is a grid.

2. **Query the headline TBR** (sandbox quick check, or HPC in a campaign):
   ```bash
   # Human-readable
   python3 -m salt_neutronics.cli tbr --bef2 33.3 --li6 0.075
   # Machine-readable JSON (preferred when you will parse it)
   python3 -m salt_neutronics.cli tbr --bef2 40 --li6 0.5 --json
   ```

3. **Read the result.** The JSON report carries everything needed to interpret and cite
   it: `result.tbr`, `result.interpretation`, `input_composition`,
   `derived.beryllium_multiplier`, and `provenance.is_interpolated` /
   `provenance.is_extrapolated`. An **extrapolated** number is not backed by any Shift
   run — always say which it is.

4. **Sweep / plot** (sandbox). See [Other queries](#other-queries-sweeps-flux-density-plots).

## The key input mapping: BeF₂ mol % → beryllium multiplier

Users think in **salt composition**; the precomputed data is indexed by **beryllium
multiplier**. The skill converts:

```
beryllium_multiplier = (mol% BeF2) / 33.33
```

Eutectic FLiBe (2 LiF : 1 BeF₂ = 33.33 mol% BeF₂) maps to multiplier **1.0**. The
scanned grid (multiplier 0.9–1.4) covers roughly **30–46.67 mol% BeF₂**. Valid query
ranges:

- BeF₂: **30–46.67 mol %**
- Li-6 enrichment: **0.07–1.0**

Single-point queries outside these ranges are rejected (pass `--allow-extrapolation` to
override); sweeps return `NaN` for out-of-grid points. See `references/physics.md` in
the clone for the assumption behind the mapping and how to change the nominal
(`--nominal-bef2`).

## Running the TBR sim on HPC (the HPC step)

The query is wrapped as the VISTA HPC job **`salt-neutronics-tbr`**, with CPU backends for
both **Odo** (the OLCF open enclave) and **Perlmutter** (NERSC) — pick with `cluster=`.
Both run on a single CPU core; one submission = one state point = one `results.json`. The
job clones the repo on the node, uses a tiny Python env at runtime
(numpy/scipy/h5py/matplotlib — seconds, no pre-provisioning), runs the `tbr` query, and
writes `results.json` into the job's output dir.

```python
submit_hpc_job(
    job="salt-neutronics-tbr",
    cluster="odo",            # or cluster="perlmutter"
    duration="0:10:00",
    script_args="--bef2 33.33 --li6 0.075",
)
```

Use whichever cluster the user has credentials for; the `script_args` contract and the
`results.json` output are identical across backends.

### `script_args` contract

Pass one state point's composition as a single flat string (forwarded to the `tbr`
query):

| Flag | Meaning | Default |
|---|---|---|
| **Composition (pick one):** | | |
| `--bef2 P` | BeF₂ content in mol% | — |
| `--be-multiplier M` | beryllium multiplier (instead of `--bef2`) | — |
| `--li6 E` | Li-6 enrichment (⁶Li atom fraction) | 0.075 |
| `--nominal-bef2 P` | mol% BeF₂ that maps to multiplier 1.0 | 33.33 |
| `--allow-extrapolation` | permit (cautious) extrapolation outside the grid | off |

### Outputs (in the job's `$VISTA_OUT`)

- `results.json` — the result. TBR at `result.tbr`; `provenance.is_extrapolated`
  flags out-of-grid; `input_composition` + `derived.beryllium_multiplier` echo the
  state point. (Full schema: `references/data_schema.md` in the repo.)

Poll with `get_hpc_job_status`; when complete, fetch with
`get_hpc_job_outputs(job_id, files=["results.json"], cluster=…)` (the same cluster you
submitted to).

## Sim-skill contract for multi-agent campaigns

In a VISTA campaign, the planner fans out **one neutronics subagent per candidate state
point**; each subagent uses this skill to launch and parse one Odo job. The two
operations a subagent performs:

- **dispatch(order) → job:** turn the order (a composition + Li-6 enrichment) into the
  `script_args` string above, call
  `submit_hpc_job(job="salt-neutronics-tbr", cluster="odo"|"perlmutter", duration="0:10:00", script_args=…)`,
  and record the returned `job_id` + `cluster`.
- **collect(job) → result:** once the job completes,
  `get_hpc_job_outputs(... files=["results.json"])` and parse it into a structured
  result:
  ```json
  {"tbr": <float>, "is_extrapolated": <bool>,
   "bef2_mol_percent": <float>, "li6_enrichment": <float>,
   "beryllium_multiplier": <float>, "lif_mol_percent": <float>,
   "shielding_flux_n_per_cm2_s": <float>}
  ```
  If `provenance.is_extrapolated` is true, flag the point as not backed by a Shift run
  rather than reporting it as a simulated value.

  **Also extract the magnet radiation-shielding flux** into `shielding_flux_n_per_cm2_s`,
  reading it from the report's `shielding.magnet_flux_n_per_cm2_s` (the neutron flux reaching
  the superconducting magnets behind a 1 m blanket, n/cm²·s). The planner's scorer treats this
  as a **hard gate**: a candidate whose flux exceeds **1e12** is rejected (shielding too weak);
  **≤ 1e10** is the preferred long-magnet-life target. Report it alongside the TBR so the scorer
  can apply the gate.

Subagents do not talk to each other; the planner and durable campaign state coordinate
them. To screen a grid, the planner issues N orders (one per (composition, Li-6)) and
dispatches N jobs in parallel, then collects and hands the results to the planner's
scorer for ranking/plotting.

**Coupled with density.** A breeder-salt screen usually wants **both** TBR and mass
density per composition. The planner can fan out, per state point, a `salt-neutronics-tbr`
job (TBR, Odo/CPU, this skill) **and** a [`salt-chemistry-md`](../salt-chemistry-md/SKILL.md)
job (density, Frontier/GPU), then rank candidates on both axes (e.g. TBR ≥ 1.1 *and*
acceptable coolant density).

## Other queries (sweeps, flux, density, plots)

These are sandbox operations (a sweep is many interpolations; plotting needs no HPC):

```bash
# Sweep TBR over composition; write a CSV (+ optional surface plot)
python3 -m salt_neutronics.cli sweep --bef2-range 30 46 60 --li6 0.075 --plot
# 2-D sweep over both axes
python3 -m salt_neutronics.cli sweep --bef2-range 30 46 40 --li6-range 0.07 1.0 40 --output sweep.csv

# Neutron flux at a spatial position (cm) for one composition
python3 -m salt_neutronics.cli flux --position 15 --bef2 33.3 --li6 0.075 --json

# Number density of a nuclide by ZAID (1003 = tritium, 8016 = O-16, ...)
python3 -m salt_neutronics.cli density --zaid 1003 --position 15 --bef2 33.3 --li6 0.5 --json
```

`--bef2-range LOW HIGH [N]` builds `N` linearly spaced points (default 50). Sweep CSV
columns: `bef2_mol_percent, li6_enrichment, beryllium_multiplier, tbr`. For finer plot
control, import the helpers (`from salt_neutronics.plotting import plot_tbr_surface,
plot_flux_profiles, plot_nuclide_density_surface`); each saves a PNG and returns its
path.

## Interpreting results for the user

Always state **whether the value was interpolated or extrapolated**
(`provenance.is_extrapolated`) — an extrapolated number is not backed by any simulation.
Then read the physics:

- **TBR ≥ 1.0** is the self-sufficiency threshold; **≥ ~1.1** gives a comfortable margin
  once first-wall losses, structural absorption, and neutron streaming are accounted for.
- In this dataset TBR **rises with both** Li-6 enrichment and BeF₂ content, and Li-6
  enrichment is the **stronger lever** (especially below ~50%). State the trend, not just
  the single number, when it helps the user's decision.
- This is an **emulation by interpolation**, not a fresh transport solve. Don't claim
  Monte Carlo statistical uncertainties; the limiting error is interpolation between
  grid points.

## Reference material (in the cloned repo)

- `references/physics.md` — FLiBe chemistry, TBR definition, the composition→multiplier
  assumption and how to change it, modeling caveats. **Read before judging whether a
  result is trustworthy.**
- `references/data_schema.md` — exact layout of `neutronics_isotopics.h5`.
- `README.md` — human-oriented overview, install, and dev/test instructions.

## Guardrails

- Always report **interpolated vs extrapolated**; never present an extrapolated TBR as a
  simulated result.
- Composition is given in **mol% BeF₂**; the skill derives the beryllium multiplier from
  it — report the multiplier the run actually used (`derived.beryllium_multiplier`).
- Sanity-check the trend (TBR rises with Li-6 and BeF₂); nominal eutectic FLiBe at
  natural Li-6 sits near TBR ≈ 1.0.
- In a campaign, never launch jobs without an approved plan, and cite the
  `job_id` / `results.json` for every number you report.
