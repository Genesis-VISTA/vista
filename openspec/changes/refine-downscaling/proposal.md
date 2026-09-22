## Why

VISTA's only climate entry, `water4energy-diagnostic`, is a CPU model-evaluation
diagnostic over fixed climatologies. REFINE (Resolution-Enhancement Framework
Integrating Artificial Intelligence for Natural and Energy Systems) is the
complementary capability: **AI model inference on Frontier GPUs** that downscales
daily Daymet `tmin`/`tmax`/`prcp` from 1/4 degree to 1/24 degree (~25 km -> ~4 km,
6x) with a pretrained terrain-aware transformer. Today a scientist runs it by hand
from a shared Frontier directory.

Packaging it as a skill gives the agent the submit -> fetch -> display -> interpret
loop over a real ML inference workload. It is the catalog's first **GPU** job and
its first job whose code, data, weights *and* Python environment are all pre-staged
read-only on Lustre — nothing is cloned, built, or vendored.

## What Changes

- New curated HPC job `hpc_jobs/refine-downscaling/` (Frontier only, 1 node, GPU).
  It runs the demo package **in place** from
  `/lustre/orion/world-shared/cli138/haoran/GM_Downscaling_demo1` against the
  world-shared `torch_rocm` conda env, and writes only into `$VISTA_OUT`.
- **One job, two modes** selected by `script_args`: `--mode infer` (default) runs
  `pipeline_04_infer.py` for N days; `--mode evaluate` runs
  `pipeline_03_evaluate.py` against the held-out 1990 test split with its bilinear
  baseline, and `--plots` adds `utility_plot_spatial_statistics.py` comparison maps.
- A `results.json` composed from the three structured JSON files the upstream
  pipelines already emit — no stdout scraping — plus provenance and a wrapper-drawn
  quicklook figure for `infer`, which upstream has no plot for.
- New skill `backend/src/vista_backend/db/skills/refine-downscaling/SKILL.md`:
  trigger description, mode/`script_args` contract, output contract, reference
  metrics, and interpretation guardrails.
- Wiring into the **existing** `water4energy` project and system prompt, with
  explicit routing between the two skills.
- Hermetic tests only; a manual Frontier run validates numbers outside CI.

## Capabilities

### New Capabilities

- `refine-downscaling`: Frontier GPU super-resolution downscaling delivered as a
  VISTA skill and HPC job.

### Modified Capabilities

- `hpc-job-contracts`: the catalog gains a GPU entry that depends on a
  pre-provisioned environment and read-only inputs it must preflight rather than
  create, and whose bulk output deliberately stays on Lustre.

## Non-goals

- **No training or data preparation** (`pipeline_01/02`). Multi-node, long, and
  allocation-expensive; the agent must not be able to start one.
- **No new grids, variables, regions, or years.** The route is the single 6x
  `tmin`/`tmax`/`prcp` model; `stage2` is its legacy registry identifier.
- No vendoring or runtime cloning of the demo repo, whose large assets are
  gitignored and whose remote is SSH-only.
- No campaign manifest, no VISTAGuard, no live/`hpc`-marked CI lane.

## Impact

- `hpc_jobs/` — new entry; catalog contract tests auto-parametrize over it
- `backend/src/vista_backend/db/skills/`, `db/system_prompts/water4energy.md`,
  `db/seed.py` (skills list only — no new project)
- New `backend/tests/test_refine_downscaling_skill.py`,
  `mcp_servers/vista_mcp_server/tests/test_refine_downscaling_job.py`
- No changes to shared dispatch code in `mcp_servers/vista_mcp_server/src`
