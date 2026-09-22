---
name: refine-downscaling
description: >-
  Downscale daily climate fields with a pretrained AI model on OLCF Frontier GPUs —
  REFINE super-resolution of Daymet daily minimum temperature (tmin), maximum
  temperature (tmax), and precipitation (prcp) from 1/4 degree to 1/24 degree, about
  25 km to 4 km, a 6x resolution increase with terrain awareness. Also evaluates that
  model against the held-out 1990 Daymet truth split, reporting bias, MAE, and RMSE
  against a bilinear-interpolation baseline, and renders standard spatial-statistics
  comparison maps. Use this skill whenever the user wants to downscale, super-resolve,
  refine, or increase the resolution of climate or weather data; produce high-resolution
  or km-scale temperature or precipitation fields from coarse model or reanalysis
  output; run REFINE, the Resolution-Enhancement Framework, or the Water4Energy
  downscaling demo; work with Daymet daily tmin/tmax/prcp; ask how much better an AI
  downscaling model is than interpolation; or evaluate a downscaling checkpoint — even
  if they do not say "REFINE", "Daymet", or "Frontier" explicitly. This is AI model
  inference that increases spatial resolution; it is NOT a climate simulation, NOT a
  bias-correction of a climate model against observations, and NOT a general regridding
  tool.
metadata:
  version: "0.1.0"
  tags: ["OLCF", "Frontier", "GPU", "Climate", "Water4Energy", "Downscaling", "Machine Learning", "HPC"]
author: Haoran Niu and Deeksha Rastogi
---

# REFINE 6x downscaling

This skill answers one question well: **what do coarse daily Daymet fields look like
at ~4 km, and how much of that detail is real?** It runs a pretrained terrain-aware
REFINE transformer on OLCF Frontier GPUs and returns downscaled fields, skill metrics
against held-out truth, and figures.

REFINE is the Resolution-Enhancement Framework Integrating Artificial Intelligence for
Natural and Energy Systems. The model maps a 228x516 grid at 1/4 degree onto a
1368x3096 grid at 1/24 degree — 6x in each direction — for `tmin`, `tmax`, and `prcp`
together. It has 5.18 M parameters.

Provenance: the analysis code is the `Water4Energy_AgenticDemo` package by Haoran Niu
and Deeksha Rastogi, run **unmodified and in place** from its read-only staging
directory on Lustre. VISTA carries only this SKILL.md and a thin HPC job
(`hpc_jobs/refine-downscaling`). Original Daymet data is Thornton et al. (2021).

## Scope, stated plainly

| | |
|---|---|
| **Model** | Fixed: one pretrained 6x checkpoint. `stage2` is its legacy registry name. |
| **Variables** | Fixed: `tmin`, `tmax`, `prcp` — always all three, together. |
| **Grid** | Fixed: the Daymet grid, 1/4 degree in, 1/24 degree out. No regridding of any kind happens. |
| **Years** | Fixed: 1980–1990 are staged. Train 1980–1987, val 1988–1989, **test 1990**. |
| **Tunable** | Mode, interval length, start date, split, whether to draw spatial plots. |

If the user wants a different model, variable, region, grid, or period, say so directly
— it is not supported today and would need work in the upstream package. **Training is
deliberately out of scope**: it is multi-node, long, and allocation-expensive, and this
skill cannot start one. Do not silently substitute this skill for a different question.

## This skill or the other one?

This project has two climate skills and they answer opposite questions. Route carefully:

| The user is asking about | Use |
|---|---|
| resolution, downscaling, super-resolution, km-scale detail, Daymet, tmin/tmax/prcp | **this skill** |
| model bias, pattern correlation vs observations, E3SM, ERA5, TVA service area | `water4energy-diagnostic` |

"Evaluate the model" is ambiguous between them — ask which model. Here it means
*the downscaling checkpoint against Daymet truth*; there it means *E3SMv3 against ERA5*.

## Where things run

| Context | What happens here |
|---|---|
| **Frontier (1 GPU)** | the whole run — one `submit_hpc_job` |
| **Sandbox / chat** | fetch the small outputs, display figures, read `results.json`, interpret |

There is nothing to run in the sandbox. The inputs, weights, and environment are all
pre-staged on Lustre; the sandbox has no GPU and cannot reach any of it. Do not try to
reproduce the model locally.

## Workflow

**1. Submit.** One submission = one mode.

```python
submit_hpc_job(job="refine-downscaling", cluster="frontier")            # 1 day of inference
submit_hpc_job(job="refine-downscaling", cluster="frontier",
               script_args="--mode evaluate --max-days 3 --plots")
```

`script_args` is optional — with none, one day of inference from 1990-01-01.

> **Walltime.** The 30-minute default is a placeholder inherited from the package's own
> launcher, not a measurement. If a longer interval times out, raise `duration` rather
> than assuming the job is broken. There is no environment cold start: the conda env is
> pre-provisioned, and a missing one fails immediately instead of rebuilding.

**2. Poll — slowly.** Give the user the `job_id` first so they can walk away. Then:

```text
get_hpc_job_status(job_id)
  -> COMPLETED / FAILED / CANCELED : stop
  -> anything else                 : run_bash "sleep 45", then poll again
```

**Never poll back-to-back.** Frontier logs are cached server-side for 30 s, so a faster
poll returns byte-identical output and buys nothing; the queue wait dominates anyway;
and the project's shared `request_limit` can be exhausted before the job even starts.
Stop after ~20 polls and hand back the state and the `job_id` — a long queue wait is not
a failure. Do not narrate every poll.

**3. Fetch the small things.**

```python
get_hpc_job_outputs(job_id, cluster="frontier",
                    files=["results.json", "quicklook_tmin.png"])
```

**Never fetch the NetCDF by reflex.** It is ~51 MB per day. `results.json` lists its
path and exact byte size under `artifacts`; quote that size and ask before fetching it.

**4. Display** each PNG with `display_file(<path>)`.

**5. Interpret** from `results.json`. Report numbers *with* the figures; never post
figures alone.

## `script_args` contract

`--mode infer` (default) — downscale days.

| Flag | Meaning | Default |
|---|---|---|
| `--days N` | days to downscale | 1 |
| `--start-date YYYY-MM-DD` | first day; must stay inside one calendar year | 1990-01-01 |
| `--batch-size N` | inference batch size | 1 |
| `--allow-large` | permit more than 31 days | off |
| `--checksum-inputs` | SHA256 the inputs into provenance | off |

`--mode evaluate` — score the checkpoint against truth.

| Flag | Meaning | Default |
|---|---|---|
| `--split {train,val,test}` | which prepared split | test |
| `--max-days N` | days to evaluate | 1 |
| `--plots` | also render spatial-statistics maps | off |
| `--tile-rows N` | plotting memory knob | 21 |

**Modes do not mix.** Passing an evaluate flag to `infer` (or the reverse) is an error,
not a silent no-op — the job refuses before doing any work, so a mistake costs seconds
rather than a wasted allocation. `--plots` requires `--mode evaluate`, because spatial
statistics are computed against truth and inference has none.

## Cost, and the day guardrail

The job **refuses more than 31 days** without `--allow-large`, and says what the output
would have been. That refusal is a feature: `--days` maps onto a zero-based exclusive
index, so a full year is one keystroke from a single day.

| | per day | full year (365) |
|---|---|---|
| inference NetCDF | ~51 MB | ~18.5 GB |
| retained predictions (`--plots` only) | ~48 MB | ~18 GB |

Before passing `--allow-large`, state the projected volume and get the user to agree.
Prefer a short interval first: a 1-day run proves the pipeline end to end.

## Reading `results.json`

```json
{
  "mode": "evaluate",
  "figures": ["metrics_comparison.png", "evaluation/mean_tmin_comparison.png"],
  "artifacts": [{"path": "inference-4408123.nc", "bytes": 53477376, "kind": "netcdf"}],
  "metrics": {
    "tmin": {
      "model":             {"bias": -0.00102, "mae": 0.04966, "rmse": 0.16798},
      "bilinear_baseline": {"bias": -0.00067, "mae": 0.20163, "rmse": 0.99392},
      "mae_improvement_percent": 75.37, "count": 1545894720
    }
  },
  "run": {"split": "test", "days": 365,
          "temperature_order_violation_fraction": 0.0142},
  "provenance": {"checkpoint_sha256": "2e37007658d2...", "gpu": "...",
                 "cuda_available": true, "wall_seconds": 0.0, "slurm_job_id": "..."}
}
```

- **Units are per variable**: `tmin`/`tmax` in **degC**, `prcp` in **mm/day**. Never
  report a bare number; always attach the unit. They are in `run.variable_metadata`.
- **`bias` is model minus truth.** Positive means the model runs warm or wet.
- **`bilinear_baseline` is interpolation, not a competing model.** Label it that way.
  `mae_improvement_percent` is the model's MAE reduction relative to it.
- **`count`** is the number of valid grid cells aggregated — 1.5 billion for a full
  year, a few million for one day. It is how you tell a smoke test from the reference.
- **`cuda_available: false` invalidates the timing**, not the numbers: the run fell back
  to CPU. Say so rather than reporting the wall time as representative.

Every number here is read from JSON the pipelines themselves wrote — nothing is scraped
from logs. If the job could not read that JSON it fails outright rather than reporting
nulls, so a `results.json` you receive is trustworthy or absent.

## Interpreting the result

### The reference: what the released model actually does

Full-year 1990 test split, 365 days, all three variables (from the package's model
registry). **These are the numbers to compare against.**

| Variable | bias | MAE | RMSE | baseline MAE | MAE improvement |
|---|---|---|---|---|---|
| `tmin` (degC) | −0.0010 | 0.0497 | 0.1680 | 0.2016 | **75.4 %** |
| `tmax` (degC) | +0.0014 | 0.0487 | 0.1535 | 0.2012 | **75.8 %** |
| `prcp` (mm/day) | −0.0009 | 0.0495 | 0.3137 | 0.1347 | **63.2 %** |

The headline is the improvement column: the model cuts interpolation error by about
three quarters for temperature and just under two thirds for precipitation.

### A short run is not the reference

**This is the trap most likely to catch you.** `--max-days 1` gives a one-day number
from 1 January. The table above is 365 days. A single winter day is not a sample of the
year, and its metrics can differ substantially without anything being wrong. When you
report a short run:

- say how many days it covered, every time;
- compare it to the reference as an *order-of-magnitude* check, not a match;
- never present a 1-day result as reproducing, confirming, or contradicting the
  published full-year metrics.

Same rule for `--days` in infer mode: a 1-day downscale demonstrates the pipeline, not
the model's annual skill.

### Precipitation error is heavy-tailed — that is expected

Look at RMSE ÷ MAE: **~3.4 for `tmin`, ~3.2 for `tmax`, but ~6.3 for `prcp`.** RMSE
punishes large errors quadratically, so a ratio that high means precipitation error is
concentrated in a few wet cells rather than spread evenly. That is the nature of daily
rainfall, not a broken run or a weak model.

So do **not** report "the model is much worse at precipitation" on the strength of
RMSE 0.31 vs 0.17. The like-for-like statement is the improvement over baseline: 63 %
for `prcp` against 75 % for temperature — a real but far more modest gap. Report both
MAE and RMSE for precipitation, and say which one you are reasoning from.

### Near-zero bias is not a clean bill of health

Every bias in the table is about 0.001 while MAE is about 0.05 — fifty times larger.
Bias is a *mean* error, so positive and negative errors cancel. A near-zero bias with a
non-trivial MAE means the model is unbiased on average and still wrong cell by cell.
Report bias alongside MAE and RMSE, never instead of them.

### Physical consistency

`temperature_order_violation_fraction` is the share of valid cells where the raw model
predicted `tmin > tmax`. In the reference run it is **0.0142 — about 1.4 %.** The job
always runs with `--enforce-temperature-order`, which repairs those cells to their
midpoint, so the delivered fields are consistent. Worth mentioning when the user asks
whether the output is physically sound: the answer is yes, *after* an enforced
correction that affected ~1.4 % of cells, and that number is a genuine measure of how
hard the model finds the constraint.

### Reading the figures

- **Quicklook** (`infer`): coarse input beside the 6x output for one timestep, on a
  **shared color scale** so the two are comparable, with percentile limits so a few
  extreme cells cannot flatten the field. Axes are **grid indexes, not longitude and
  latitude** — the geographic coordinates live in the Daymet files and no regridding
  occurs. Do not describe positions as coordinates.
- **`metrics_comparison.png`** (`evaluate`): model vs bilinear MAE and RMSE per
  variable. It renders the same numbers as `metrics`; it adds no information.
- **Spatial statistics** (`evaluate --plots`): per-statistic comparison maps.
  `spatial_statistics.statistics` names which ones were produced.

## Guardrails

- **Never present figures as the result.** A figure plus the metrics that quantify it
  is a result; a folder of PNGs is a data dump.
- **Always state the interval.** Number of days, and the split or start date.
- **Cite the `job_id`**, and the `checkpoint_sha256` whenever comparability matters.
- **Attach units** to every metric: degC or mm/day.
- **Call the baseline a baseline.** Bilinear interpolation is a reference point, not a
  rival model.
- **Never compare metrics across different splits, intervals, or checkpoints.** Two
  runs are comparable only if all three match.
- **Do not fetch the NetCDF without asking**, and quote its size from `artifacts` first.
- **Do not claim geographic locations from the quicklook** — its axes are grid indexes.
- **A completed job is not a validated model.** Exit zero means the pipeline ran.
- **If preflight fails, report the exact path and stop.** These assets are pre-staged
  read-only and resubmitting cannot recreate them.
- **A checkpoint hash mismatch is disqualifying.** It means the staged weights are not
  the released model, so nothing is comparable to the reference table. Report it; do not
  work around it.

## Troubleshooting

| Symptom | What it means |
|---|---|
| `ERROR: conda env not found at ...` | the pre-provisioned ROCm env moved; needs a human, not a retry — the job never builds one |
| `ERROR: missing required input ...` | that asset is not staged; a git clone cannot supply it (the large files are gitignored upstream) |
| `checkpoint SHA256 mismatch` | staged weights are not the released 6x model; stop and report |
| refused: `--days N exceeds the 31-day guardrail` | intended; confirm the volume with the user before `--allow-large` |
| refused: `--plots requires --mode evaluate` | spatial statistics need truth; inference has none |
| refused: `only applies to --mode ...` | a flag from the other mode; re-read the contract above |
| `runs past the end of <year>` | intervals cannot cross a calendar year; split the request |
| `warnings: torch reports no GPU` | the run fell back to CPU — the numbers stand, the timing does not |
| `did not write its expected output` | the upstream pipeline's contract changed; a tooling problem, not a science result |

Full job contract, output schema, and environment behavior:
`hpc_jobs/refine-downscaling/README.md`.
