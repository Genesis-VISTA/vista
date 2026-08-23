---
name: water4energy-diagnostic
description: >-
  Evaluate the E3SMv3 climate model against ERA5 reanalysis — 1985–2014 annual-mean
  surface temperature and precipitation — on a common global grid, with a regional
  focus on the TVA Power Service Area in the southeastern United States. Produces
  four-panel comparison figures (observation, model, model-minus-observation bias,
  and scatter plus a metrics table) and area-weighted skill metrics: pattern
  correlation, RMSE, and bias. Runs on OLCF Frontier through VISTA's HPC backend.
  Use this skill whenever the user wants to compare a climate model to observations
  or reanalysis; evaluate or validate E3SM, E3SMv3, or an E3SM historical run;
  work with ERA5; look at annual-mean or climatological surface temperature (TS/ts)
  or precipitation (PRECC/PRECL/pr); assess model bias, warm/cold bias, wet/dry
  bias, pattern correlation, or RMSE against observations; ask how well a model
  reproduces temperature or rainfall over the Tennessee Valley, TVA, the TVA
  service area, or the southeastern US; or run the water4energy / Water4Energy
  diagnostic — even if they do not say "E3SM", "ERA5", or "Frontier" explicitly.
  This is a model-evaluation diagnostic on fixed pre-staged climatologies, not a
  climate simulation and not a general-purpose regridding tool.
metadata:
  version: "0.1.0"
  tags: ["OLCF", "Frontier", "Climate", "Water4Energy", "Model Evaluation", "HPC"]
author: Water4Energy team
---

# ERA5 vs E3SMv3 climatology diagnostic

This skill answers one question well: **how closely does E3SMv3 reproduce observed
annual-mean surface temperature and precipitation, globally and over the TVA Power
Service Area?** It runs the `water4energy_diagnostic` package on OLCF Frontier and
returns four figures plus area-weighted skill metrics.

Provenance: the analysis program is the public repo
[`daliwang/water4energy_diagnostic`](https://github.com/daliwang/water4energy_diagnostic),
run **unmodified**. VISTA carries only this SKILL.md and a thin HPC job
(`hpc_jobs/water4energy-diagnostic`) that clones the repo on the compute node. It
was verified on Frontier `login04` on 2026-08-22 at ~37 s wall time.

## Scope, stated plainly

| | |
|---|---|
| **Datasets** | Fixed: one ERA5 and one E3SMv3 annual climatology, pre-staged read-only on Lustre. You do not choose them. |
| **Variables** | Fixed: surface temperature (°C) and precipitation (mm/day). |
| **Region** | Fixed: the TVA Power Service Area polygon, plus global. |
| **Tunable** | Comparison-grid `--resolution`, figure `--dpi`. That is all. |

If the user wants a different model file, variable, region, or time period, say so
directly — it is not supported today and would need work in the upstream repo.
Do not silently substitute this diagnostic for a different question.

## Where things run

| Context | What happens here |
|---|---|
| **Frontier (CPU)** | the whole diagnostic — one `submit_hpc_job`, ~40 s of compute |
| **Sandbox / chat** | fetch the outputs, display the figures, read `results.json`, interpret |

There is nothing to run in the sandbox: the inputs are 1.25 GB and already live on
Lustre. Do not try to reproduce the analysis locally.

## Workflow

**1. Submit.** One submission = one complete diagnostic.

```python
submit_hpc_job(
    job="water4energy-diagnostic",
    cluster="frontier",
    duration="00:10:00",          # see the cold-start note below
    script_args="--resolution 1.0 --dpi 200",
)
```

`script_args` is optional — with none, the validated defaults (1.0°, 200 dpi) apply.

> **Cold start.** The job builds a Python environment on first use in a deployment
> (~2–5 min of package installs; `cray-python` lacks cartopy, matplotlib, shapely,
> xarray, and netCDF4). If a run reports `env cold` or times out, resubmit with
> `duration="00:30:00"`. Later runs reuse the cached environment and fit inside 10
> minutes comfortably.

**2. Poll** with `get_hpc_job_status(job_id)` until it completes. Compute is ~40 s;
essentially all elapsed time is queue wait.

**3. Fetch** the metrics and the figures:

```python
get_hpc_job_outputs(job_id, cluster="frontier",
                    files=["results.json",
                           "surface_temperature_comparison.png",
                           "precipitation_comparison.png"])
```

Add `diagnostic_stdout.txt` when a run looks wrong. The `.pdf` versions exist too,
for anyone who wants print-quality output.

**4. Display** each PNG with `display_file(<path>)` so the user actually sees them.

**5. Interpret** from `results.json` — see below. Report numbers *with* the figures;
never post figures alone.

## `script_args` contract

| Flag | Meaning | Default |
|---|---|---|
| `--resolution D` | comparison-grid spacing, degrees | 1.0 |
| `--dpi N` | PNG resolution | 200 |
| `--checksum-inputs` | SHA256 the inputs into provenance | off |

Keep **1.0°** unless the user asks otherwise, and push back gently if they want
finer: the E3SM file's native spacing is ~1.1–1.5° (median 1.33°), so a 0.5° or
0.25° target oversamples the model. It is permitted, but it manufactures no new
model information — and metrics from different resolutions are **not comparable**,
so never mix them in one comparison.

## Reading `results.json`

```json
{
  "metrics_source": "stdout",
  "region_name": "TVA",
  "grid": {"resolution_deg": 1.0},
  "variables": {
    "surface_temperature": {
      "units": "degC",
      "global": {"correlation": 0.9937, "rmse": 1.685, "bias": 0.324},
      "region": {"correlation": 0.9519, "rmse": 0.565, "bias": -0.371}
    },
    "precipitation": {"units": "mm/day", "global": {…}, "region": {…}}
  },
  "provenance": {"repo": {"revision": …}, "wall_seconds": …, "slurm_job_id": …}
}
```

- **`bias` is E3SM − ERA5.** Positive means the model is **warmer** or **wetter**
  than the reanalysis. Always state the sign convention when you report a bias.
- **`correlation`** is the area-weighted spatial *pattern* correlation over paired
  grid cells — it measures whether the model puts features in the right places, not
  whether the magnitudes match. Report it alongside RMSE, never instead of it.
- **`rmse`** is area-weighted and uses signed grid-cell errors, so over- and
  under-prediction cannot cancel. A near-zero bias with a large RMSE means
  compensating regional errors — worth calling out explicitly.
- All metrics are **area weighted** by `cos(latitude)` on the common grid.

**Metrics you will not find there.** The diagnostic also computes the ERA5/E3SM
means, the σ (standard-deviation) ratio, the percent-normalized RMSE, and the
grid-cell counts — but it renders them into the **fourth panel's table** and never
prints them, so they are not machine-readable. If the user asks for the σ ratio,
the means, or nRMSE, read them off the figure and say that is where they came from.
Never guess or recompute them.

## Interpreting the result

### What a healthy run looks like

Reference values from the verified Frontier run (1.0° grid, the shipped file pair):

| Variable | Scope | r | RMSE | bias |
|---|---|---|---|---|
| Surface temperature | global | 0.9937 | 1.685 °C | +0.324 °C |
| Surface temperature | TVA | ~0.952–0.957 | ~0.54–0.57 °C | ~−0.36 to −0.37 °C |
| Precipitation | global | 0.8879 | 1.047 mm/day | +0.083 mm/day |
| Precipitation | TVA | ~0.530–0.541 | ~0.249–0.250 mm/day | ~+0.21 mm/day |

The ranges are real: two documented runs of the *same* inputs differ in the third
decimal place because interpolation libraries differ between versions. **Small
differences across library versions are normal; large differences are not.** If the
numbers land in these ranges, the run is healthy — say so and move on to the
science. If a metric is off by more than roughly a factor of two, treat the run as
suspect and investigate before reporting it as a model finding.

### The one result that always looks alarming and is not

**TVA precipitation correlation is only ~0.53, and that is expected.** Two reasons,
both methodological rather than a model failure:

1. At 1.0° only **~20 grid-cell centers** fall inside the TVA polygon. A pattern
   correlation over 20 points is inherently noisy.
2. Precipitation is spatially localized, and the diagnostic uses **linear**
   interpolation, which smooths exactly the fine structure that a regional
   correlation depends on.

So do **not** report "E3SM fails to reproduce TVA rainfall" on the strength of that
number. The regional *bias* (~+0.21 mm/day, i.e. the model is slightly wet over the
TVA area) is the more meaningful regional statement. Note that this bias is small in
absolute terms but is a substantial fraction of a small regional mean — worth saying
both ways.

If a user needs a defensible regional precipitation evaluation, the honest answer is
that it requires **conservative remapping** from the E3SM source-cell mesh rather
than linear interpolation. The shipped climatology file carries cell centers and
areas but not the full mesh geometry a conservative remapper needs, so that is out
of scope here — say so rather than over-claiming.

### Why there is no temperature nRMSE

Percent-normalized error is not invariant to a change of temperature scale: dividing
by a mean in °C gives a different answer than dividing by a mean in K, because the
two scales have different zero points. So temperature is reported as **absolute**
RMSE and bias only. If a user asks for temperature error "as a percentage," explain
this rather than computing a number that depends on an arbitrary zero.

Precipitation has a true zero, so its percent-normalized metrics *are* meaningful —
they are in the figure's table panel (see above).

### Reading the figures

Each figure has four panels: (a) ERA5 climatology, (b) E3SMv3 climatology, (c) the
E3SM − ERA5 bias, (d) global and TVA scatter plus the metrics table. Maps use a
Robinson projection centered on 0° longitude, so **North America appears on the
left**. The TVA outline is magenta with a white underlay.

Two things to know before describing a map:

- **Color limits are percentile-based, not full-range** (climatology: combined
  1st–99th percentile; bias: symmetric about zero at the 98th percentile of
  |bias|). Extremes are deliberately clipped so the main spatial structure stays
  visible — do not describe the color limits as the data range.
- **The "TVA" text label sits ~3° north of the polygon centroid**, so the word
  appears slightly north of the outline it names. That is cosmetic, not a
  georeferencing error.

### One domain fact worth getting right

The magenta polygon is the **TVA Power Service Area** — the electric service
territory (~90.3°W–81.6°W, 32.3°N–37.6°N, covering Tennessee and parts of Alabama,
Mississippi, Kentucky, Georgia, North Carolina, and Virginia). It is **not** the
Tennessee River watershed. Do not describe it as a basin, watershed, or catchment.

## Guardrails

- **Never present figures as the result.** A figure plus the metrics that quantify
  it is a result; four PNGs alone is a data dump.
- **Cite the `job_id`** for every number you report, and the repo `revision` from
  `provenance` when reproducibility is at issue.
- **Report bias with its sign convention and units.** "+0.324 °C (E3SM warmer than
  ERA5)" — not "0.324".
- **Do not attribute methodological artifacts to the model.** The low TVA
  precipitation correlation is the clearest trap; interpolation smoothing and a
  20-cell sample are explanations, not model deficiencies.
- **Do not compare metrics computed at different `--resolution` values.**
- **Do not invent the figure-only metrics** (means, σ ratio, nRMSE, cell counts).
  Read them off the figure or say they are not available.
- **If the job fails preflight**, the error names the exact missing path. Report
  that path and stop — the inputs are pre-staged on Lustre and cannot be recreated
  by resubmitting. A permissions or path change needs a human.
- **A hard parse failure is intentional.** If the job reports that it could not read
  the metric summary, the upstream output format changed. Report it as a tooling
  problem; do not hand-read numbers out of the log and present them as verified.
- **This diagnostic reads its inputs read-only** and never modifies them.

## Troubleshooting

| Symptom | What it means |
|---|---|
| `env cold` then walltime exceeded | first run in the deployment; resubmit with `duration="00:30:00"` |
| `ERROR: data dir not readable` | `W4E_DATA_DIR` moved, or group read was revoked for VISTA's submitting account — needs a human, not a retry |
| `ERROR: missing required input` | the named climatology is not staged; the two NetCDFs are gitignored upstream and cannot be obtained by cloning |
| `could not read the metric summary` | upstream print format drifted; the job's regex needs updating |
| Fewer than three grid cells in the TVA boundary | `--resolution` too coarse for the polygon; use 1.0° or finer |
| Figures exist but no `results.json` | the run failed after plotting — read `diagnostic_stdout.txt` |

Full job contract, output schema, and environment behavior:
`hpc_jobs/water4energy-diagnostic/README.md`.
