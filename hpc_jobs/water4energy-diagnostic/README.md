# water4energy-diagnostic

ERA5 vs E3SMv3 annual-climatology comparison (1985–2014 surface temperature and
precipitation, with a TVA Power Service Area regional focus) on OLCF Frontier.
One submission = one full diagnostic = four figures.

The job clones the public `water4energy_diagnostic` repo at runtime, ensures a
`cray-python/3.11.7` venv, and runs `plot_e3sm_era5.py` unmodified against
climatology files that are **pre-staged read-only on Lustre**. No analysis code is
vendored into VISTA — only `run_diagnostic.py`, a thin wrapper that resolves paths,
preflights the inputs, and collects the figures.

This is the HPC backend for the `water4energy-diagnostic` skill.

Default nodes: 1 (CPU only — no GPU)
Default time: 600 s / 10 min. **Raise to `00:30:00` for the first run on a
deployment**, which pays a cold venv build (see *Environment* below).
Default queue: `batch`.

Upstream: <https://github.com/daliwang/water4energy_diagnostic> — verified on
Frontier `login04` 2026-08-22, ~37 s wall once the environment existed.

## Script args

Passed through to `run_diagnostic.py` as one flat string. All are optional; the
defaults are the validated configuration.

| Flag | Meaning | Default |
|---|---|---|
| `--resolution D` | comparison-grid spacing in degrees | 1.0 |
| `--dpi N` | PNG resolution | 200 |
| `--checksum-inputs` | SHA256 the climatologies into provenance | off |
| `--era5 PATH` | ERA5 climatology; bare name resolves in `$W4E_DATA_DIR` | `ERA5_ANN_198501_201412_climo.nc` |
| `--e3sm PATH` | E3SMv3 climatology; same resolution rule | `v3.LR.historical_0101_ANN_198501_201412_climo.nc` |
| `--tva-boundary PATH` | region polygon (GeoJSON); resolves in `$W4E_DATA_DIR` then the clone | `tva_power_service_area.geojson` |

Example:

    submit_hpc_job(job="water4energy-diagnostic", cluster="frontier",
                   duration="00:10:00", script_args="--resolution 1.0 --dpi 200")

1.0° is the right grid for the shipped E3SM file: its native nearest-neighbour
spacing is ~1.1–1.5° (median 1.33°). Finer targets are accepted but oversample the
model and create no independent model information.

## Outputs (in `$VISTA_OUT`)

    results.json                           machine-readable metrics + provenance
    surface_temperature_comparison.png     four-panel figure: ERA5, E3SM, bias, scatter+metrics
    surface_temperature_comparison.pdf
    precipitation_comparison.png
    precipitation_comparison.pdf
    diagnostic_stdout.txt                  the run log, including the metric summary
    plots/                                 the program's own output dir (same figures, plus .cartopy/)

The four figures are copied to the top level so they can be fetched by name:

    get_hpc_job_outputs(job_id, cluster="frontier",
                        files=["results.json",
                               "surface_temperature_comparison.png",
                               "precipitation_comparison.png"])

## `results.json`

    {
      "schema": "vista/water4energy-diagnostic/results/v1",
      "status": "ok",
      "metrics_source": "stdout",
      "region_name": "TVA",
      "grid": {"resolution_deg": 1.0},
      "variables": {
        "surface_temperature": {
          "units": "degC",
          "global": {"correlation": 0.9937, "rmse": 1.685, "bias": 0.324},
          "region": {"correlation": 0.9519, "rmse": 0.565, "bias": -0.371}
        },
        "precipitation": {"units": "mm/day", "global": {...}, "region": {...}}
      },
      "figures": ["precipitation_comparison.pdf", ...],
      "provenance": {
        "repo": {"url": ..., "ref": ..., "revision": <full SHA>},
        "inputs": {"era5": {"path": ..., "bytes": ..., "sha256": <only with --checksum-inputs>}, ...},
        "python": "3.11.7", "packages": {"cartopy": "0.25.0", ...},
        "wall_seconds": 41.2, "hostname": ..., "slurm_job_id": ...
      }
    }

`metrics_source: "stdout"` is not a fallback — it is how the numbers are obtained.
The upstream program is run **unmodified**, and the three metrics it prints per
variable per scope are what `results.json` carries:

    Surface temperature:
      global: r=0.9937, RMSE=1.685 °C, bias=+0.324 °C
      TVA:    r=0.9519, RMSE=0.565 °C, bias=-0.371 °C

**Known limitation.** `weighted_performance_metrics` upstream also computes the
ERA5/E3SM means, the percent-normalized RMSE and relative bias, the σ ratio, and
the cell counts — but those reach the **figure's table panel only**, never stdout.
They are therefore not machine-readable here: read them off the figure. Making them
available would need a `--json-out` (or equivalent) flag added upstream in
`daliwang/water4energy_diagnostic`; the metrics schema above is designed to absorb
the extra keys without a version bump if that ever lands.

A partial parse is a hard error: if the printed format drifts, the job fails and
names the missing metrics instead of writing a `results.json` full of nulls.

## Inputs (prerequisite — pre-staged, not produced by this job)

Unlike every other job in this catalog, the science inputs already exist on Lustre
and are only read. `W4E_DATA_DIR` in `cluster_defaults.json` must hold:

| File | Size | SHA256 (2026-08-22) |
|---|---|---|
| `ERA5_ANN_198501_201412_climo.nc` | 1.1 GB | `04489cbe1d16c888b1a96f9f2963c9f98172611c6d9fe49ec17d3533f04026ac` |
| `v3.LR.historical_0101_ANN_198501_201412_climo.nc` | 153 MB | `9d749b52eb07308566074c9a2b212f25eb4f14f9af8dbdf5c45db809d7216aa2` |

They are gitignored upstream (too large), so a clone alone is not enough. The
region polygon *does* ship in the repo.

Note that VISTA charges Frontier jobs to its own shared account, which is not the
project that owns the default `W4E_DATA_DIR` — that path must stay group-readable
by VISTA's submitting account. `setup_frontier.sh` checks this before the job does
any work and names the offending path if it fails.

## Environment (self-healing)

`cray-python/3.11.7` provides numpy and scipy but **not** cartopy, matplotlib,
shapely, xarray, or netCDF4, so a venv is required. The job:

1. uses the shared venv at `$W4E_ENV` if it imports all seven packages — a warm
   run adds seconds;
2. builds it there under an `flock` if the path is absent and creatable;
3. otherwise builds a throwaway venv in `$VISTA_OUT` (~2–5 min of proxied wheels).

A shared env that exists but fails the import check is **left alone** — another job
may be running from it — and the run falls back to a per-job venv with a warning.
Delete it by hand to re-enable the cache. There is no manual setup step: case 3
always works, so the shared cache is an optimization, not a prerequisite.

The only network need is the first `pip install`, which uses the OLCF HTTP proxy
that VISTA exports into the job environment. Plotting itself is offline: the
bundled Natural Earth 110 m coastline is passed with `--cartopy-data`.

## Notes for maintainers

- **No `#SBATCH` directives.** VISTA submits an IRI JobSpec (`executable: bash`,
  `arguments: ["-l","-c", …]`) with `job.frontier.slurm` inlined as the body, so
  `#SBATCH` lines would be inert comments. Walltime and queue live in
  `cluster_defaults.json` (`frontier.duration` in seconds, `frontier.iri.queue_name`).
  Frontier's `debug` is a QOS rather than a partition, and the IRI attributes have
  no `qos` field, so this job runs in `batch`; a 1-node/10-minute job is good
  backfill regardless.
- **No inner `srun`.** 1-rank CPU job, same shape as `hpc_jobs/example`.
- **Source staging is skipped when `<frontier_remote_dir>/<job>/src` is already
  populated.** After editing `run_diagnostic.py`, clear that dir or the cluster
  keeps running the old copy.
- Pin `W4E_REPO_REF` to a SHA when reproducibility matters more than freshness.
