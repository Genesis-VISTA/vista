# refine-downscaling

REFINE 6x AI downscaling of daily Daymet `tmin` / `tmax` / `prcp` from 1/4 degree
to 1/24 degree (~25 km -> ~4 km) as GPU inference on OLCF Frontier, plus
evaluation against the held-out 1990 truth split and standard comparison plots.
One submission = one mode = one `results.json`.

REFINE is the Resolution-Enhancement Framework Integrating Artificial Intelligence
for Natural and Energy Systems. Authors: Haoran Niu and Deeksha Rastogi.

This is the HPC backend for the `refine-downscaling` skill.

## How this job gets its code and data

**Nothing is cloned, installed, or vendored.** The complete demo package — the
`refine_downscaling` library, the pipeline scripts, the 1980-1990 Daymet inputs,
the prepared Stage 2 index, the DEMs, the frozen normalization, and the pretrained
checkpoint — is pre-staged read-only on Lustre, together with a matching ROCm
PyTorch conda environment. The job runs the upstream pipelines **in place and
unmodified**, with absolute paths, and writes exclusively into `$VISTA_OUT`.

| Env var | Default | What it is |
|---|---|---|
| `REFINE_BASE_DIR` | `/lustre/orion/world-shared/cli138/haoran/GM_Downscaling_demo1` | demo package root (read-only) |
| `REFINE_ENV` | `/lustre/orion/world-shared/cli138/haoran/envs/torch_rocm` | pre-provisioned ROCm PyTorch conda env |
| `REFINE_CHECKPOINT` | `$REFINE_BASE_DIR/artifacts/runs/refine_stage2_v1/best.pt` | pretrained 6x model |
| `REFINE_DATA_DIR` | `$REFINE_BASE_DIR/daymet/prepared` | prepared index: manifest, terrain, coords, masks, splits |
| `REFINE_INPUT_DIR` | `$REFINE_BASE_DIR/daymet/data` | coarse 0.25-degree Daymet NetCDFs |

All five are set in `cluster_defaults.json`, so relocating any of them is a config
edit, not a code change. `world-shared` matters: vista submits under the shared
`chm243` account, so the demo's own `-A cli138` launchers and its
`proj-shared/cli138` environment are not reachable from here.

**A missing environment is a hard failure, not a rebuild.** Unlike
`water4energy-diagnostic`, which self-heals a small pure-python venv, this job
refuses to continue if `REFINE_ENV` is absent or torch will not import. Building a
multi-GB ROCm PyTorch stack inside a GPU job's walltime is a support ticket, not a
retry.

Default nodes: 1, exclusive, one GPU used of the eight bound by the prolog.
Default time: 1800 s / 30 min — **a placeholder taken from the demo's own launcher
ceiling, not a measurement.** Replace it once a real run is timed.
Default queue: `batch`.

## Modes

One job, two modes, selected in `script_args`. `--mode infer` is the default.

### `--mode infer` (default)

Runs `pipeline_04_infer.py`: downscales `--days` days starting at `--start-date`,
writes NetCDF plus a wrapper-drawn quicklook figure per variable.

| Flag | Meaning | Default |
|---|---|---|
| `--days N` | days to downscale (maps to the pipeline's zero-based, exclusive `--end-index`) | 1 |
| `--start-date YYYY-MM-DD` | date of input index zero | 1990-01-01 |
| `--batch-size N` | inference batch size | 1 |
| `--allow-large` | permit `--days` above 31 | off |
| `--checksum-inputs` | SHA256 the inputs into provenance | off |

### `--mode evaluate`

Runs `pipeline_03_evaluate.py` against the prepared split, reporting the model
**and** a bilinear baseline. Predictions are discarded unless `--plots` needs them.

| Flag | Meaning | Default |
|---|---|---|
| `--split {train,val,test}` | which prepared split | test |
| `--max-days N` | cap the days evaluated | 1 |
| `--plots` | also run `utility_plot_spatial_statistics.py` | off |
| `--tile-rows N` | plotting tile height (memory knob) | 21 |
| `--batch-size N` | evaluation batch size | 1 |
| `--allow-large` | permit `--max-days` above 31 | off |

`--plots` requires retained predictions, so it is the expensive path and is bounded
by the same day guardrail. `--plots` outside `--mode evaluate` is rejected before
any work starts.

Examples:

    submit_hpc_job(job="refine-downscaling", cluster="frontier")

    submit_hpc_job(job="refine-downscaling", cluster="frontier",
                   script_args="--mode infer --days 7")

    submit_hpc_job(job="refine-downscaling", cluster="frontier",
                   script_args="--mode evaluate --max-days 3 --plots")

## The day guardrail

`--days` / `--max-days` default to **1** and the wrapper refuses more than **31**
without `--allow-large`, naming the projected output size. Inference is ~51 MB per
day (~18.5 GB for a full year) and retained predictions for a full-year evaluation
are ~18 GB. `--end-index` is zero-based and exclusive, so a full year is one typo
away — and the agent is the one typing.

## Outputs (in `$VISTA_OUT`)

    results.json                  machine-readable metrics + provenance
    quicklook_tmin.png            infer mode: coarse input vs downscaled output
    quicklook_tmax.png
    quicklook_prcp.png
    inference-<jobid>.nc          infer mode: the downscaled fields (BULK — see below)
    inference-<jobid>.nc.json     the pipeline's own run metadata
    evaluation/                   evaluate mode: evaluation_summary.json (+ predictions
                                  when --plots retained them)
    spatial_statistics/           evaluate --plots: comparison maps per statistic
    refine_stdout.txt             the run log

**Bulk output stays on Lustre.** `results.json` records the path and byte size of
the NetCDF and of any retained predictions; it does not move them into the
fetchable set. Fetch the small things:

    get_hpc_job_outputs(job_id, cluster="frontier",
                        files=["results.json", "quicklook_tmin.png"])

Ask for the `.nc` only on explicit request, after stating its size.

## `results.json`

    {
      "schema": "vista/refine-downscaling/results/v1",
      "status": "ok",
      "mode": "infer",
      "figures": ["quicklook_tmin.png", ...],
      "artifacts": [{"path": "inference-4408123.nc", "bytes": 53477376, "kind": "netcdf"}],
      "run": {                         # infer: from <output>.nc.json
        "variables": ["tmin", "tmax", "prcp"],
        "output_timesteps": 1,
        "start_date": "1990-01-01",
        "temperature_order_enforced": true
      },
      "metrics": {                     # evaluate: from evaluation_summary.json
        "tmin": {
          "units": "degC",
          "model":             {"bias": -0.00102, "mae": 0.04966, "rmse": 0.16798},
          "bilinear_baseline": {"bias": -0.00067, "mae": 0.20163, "rmse": 0.99392},
          "mae_improvement_percent": 75.37,
          "count": 1545894720
        }
      },
      "provenance": {
        "checkpoint": "...best.pt", "checkpoint_sha256": "2e37007658d2...",
        "env_prefix": "...", "torch": "2.8.0+rocm6.4", "gpu": "AMD Instinct MI250X",
        "argv": [...], "wall_seconds": 0.0, "slurm_job_id": "..."
      }
    }

Every number in `metrics` and `run` is **read from JSON the upstream pipelines
already write** — `<output>.nc.json`, `evaluation_summary.json`, and
`spatial_statistics_index.json`. Nothing is scraped from stdout. A missing or
unparseable upstream JSON is a hard failure, never a `results.json` of nulls.

`bias`, `mae`, `rmse` are model-minus-truth in each variable's own units (degC for
`tmin`/`tmax`, mm/day for `prcp`). `bilinear_baseline` is an interpolation
reference, **not** a competing trained model.

## Preflight

Before any pipeline runs, the wrapper stats the demo root, the prepared data dir,
the checkpoint, the conda env, and each required input NetCDF, and verifies the
checkpoint SHA256 against the model registry. A failure names the exact missing
path and stops — these assets are pre-staged and cannot be recreated by
resubmitting.

## Troubleshooting

| Symptom | What it means |
|---|---|
| `ERROR: conda env not found at ...` | `REFINE_ENV` moved or was removed; needs a human, not a retry |
| `ERROR: missing required input ...` | the named asset is not staged; a git clone cannot supply it (the large assets are gitignored upstream) |
| `checkpoint SHA256 mismatch` | the staged weights are not the released 6x model; stop and report rather than treating results as comparable |
| refused: `--days N exceeds 31` | intended; pass `--allow-large` only when the output volume is genuinely wanted |
| refused: `--plots requires --mode evaluate` | spatial statistics need truth, which inference does not have |
| walltime exceeded on a long interval | the 1800 s default is a placeholder; raise `duration` and prefer a shorter interval first |

Skill-side workflow and interpretation guidance:
`backend/src/vista_backend/db/skills/refine-downscaling/SKILL.md`.
