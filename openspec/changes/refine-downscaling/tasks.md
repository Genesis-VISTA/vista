## 1. HPC job package

- [x] 1.1 Create `hpc_jobs/refine-downscaling/README.md` (must start with
      `# refine-downscaling` — catalog contract) documenting the mode/`script_args`
      contract, the `results.json` schema, the outputs, and the pre-staged-asset
      prerequisite
- [x] 1.2 Add `cluster_defaults.json`: `frontier` only — `duration: 1800`,
      `node_count: 1`, `exclusive_node_use: true`, `queue_name: "batch"`, and
      `iri.environment` carrying `REFINE_BASE_DIR`
      (`/lustre/orion/world-shared/cli138/haoran/GM_Downscaling_demo1`),
      `REFINE_ENV` (`/lustre/orion/world-shared/cli138/haoran/envs/torch_rocm`),
      `REFINE_CHECKPOINT`, and `REFINE_DATA_DIR`
- [x] 1.3 Add `job.frontier.slurm`: require `VISTA_OUT` / `RUN_DIR_Frontier` /
      `REFINE_BASE_DIR`, load `PrgEnv-gnu/8.6.0` + `rocm/6.4.1` +
      `craype-accel-amd-gfx90a` + `miniforge3`, `conda activate $REFINE_ENV`, set
      `PYTHONPATH` to the demo root, point `MIOPEN_USER_DB_PATH` and
      `MPLCONFIGDIR` appropriately, then run the wrapper with `"$@"` behind an
      inner `srun ... --gpus-per-task=1 --gpu-bind=closest`. `unset PYTHONPATH`
      before `module load` (design decision 10); MIOpen caches go on node-local
      `/tmp` tagged by job id, not on Lustre (design decision 8)
- [ ] 1.4 Confirm no `#SBATCH` directive is relied on (inert — design decision 9)
      and that the GPU actually binds. Wrapper side is done: provenance carries
      `cuda_available`, `gpu`, `torch`, and `rocm`, and a CPU fallback raises a
      warning. **Confirming a real binding needs Frontier — see task 6.1.**

## 2. Wrapper and structured metrics

- [x] 2.1 Add `run_downscaling.py`: arg parsing for `--mode`, `--days`,
      `--start-date`, `--split`, `--max-days`, `--plots`, `--allow-large`,
      `--batch-size`, `--checksum-inputs`
- [x] 2.2 Preflight every required path (demo root, data dir, checkpoint, env,
      per-variable input NetCDFs) with an actionable one-line error naming the
      exact missing path; verify the checkpoint SHA256 against the model registry
- [x] 2.3 Enforce the day guardrail: default 1, refuse `> 31` without
      `--allow-large`, and state the projected output size in the refusal
      (spec: *Bounded output volume*)
- [x] 2.4 Reject `--plots` without `--mode evaluate` before any work
      (spec: *Single catalog entry with an explicit mode*)
- [x] 2.5 Invoke the upstream pipelines unmodified with absolute paths, writing
      only into `$VISTA_OUT`; propagate a non-zero exit
- [x] 2.6 Compose `results.json` (`vista/refine-downscaling/results/v1`) from
      `<output>.nc.json`, `evaluation_summary.json`, and
      `spatial_statistics_index.json` — never from stdout; hard-fail on a missing
      or unparseable upstream JSON rather than emitting nulls
- [x] 2.7 Record provenance: checkpoint path + SHA256, env path, torch/ROCm
      versions, GPU name, resolved pipeline argv, wall time, Slurm job id
- [x] 2.8 Record bulk artifacts (NetCDF, retained predictions) as path + byte size
      instead of promoting them into the fetchable set
- [x] 2.9 Render the `infer` quicklook: one coarse-vs-downscaled PNG per variable
      for the first timestep (presentation only — computes no metric)

## 3. SKILL.md

- [x] 3.1 Create `backend/src/vista_backend/db/skills/refine-downscaling/SKILL.md`
      with valid frontmatter (kebab-case `name`, trigger-rich `description` covering
      downscaling / super-resolution / Daymet / REFINE / tmin / tmax / prcp /
      1/4 degree -> 1/24 degree / Water4Energy, `metadata.version`/`tags`, `author`:
      Haoran Niu and Deeksha Rastogi)
- [x] 3.2 Document the fixed-scope table (one 6x route, three variables, Daymet
      grid, 1980–1990 staged inputs) and state plainly that training and new
      variables/regions/grids are out of scope
- [x] 3.3 Document the workflow: submit -> poll with `sleep 45` between polls ->
      `get_hpc_job_outputs` -> `display_file` each PNG -> interpret from
      `results.json`, reusing the diagnostic's polling discipline and poll cap
- [x] 3.4 Document the mode/`script_args` contract and the day guardrail, including
      why the NetCDF is not fetched by default and what it costs per day
- [x] 3.5 Write the interpretation section: the 1990 reference metrics from
      `model-registry.json` (per-variable bias/MAE/RMSE and
      `mae_improvement_percent` ~75% for tmin/tmax, ~63% for prcp), that a
      short-interval smoke test is **not** the full-year reference, and that prcp
      RMSE (~0.31) is ~6x its MAE (~0.05) versus ~3x for the temperatures because
      precipitation error is heavy-tailed — not a broken run
- [x] 3.6 Write guardrails: always pair figures with metrics; label bilinear as a
      baseline, not a competing model; cite `job_id` and the checkpoint hash; never
      compare metrics across different splits, intervals, or checkpoints; report a
      failed preflight path and stop rather than resubmitting

## 4. Wiring

- [x] 4.1 Add `refine-downscaling` to the `water4energy` project's skills in
      `db/seed.py` (no new project, no new UUID); widen the project description to
      cover both halves, and update the seed snapshot assertion in
      `backend/tests/test_water4energy_skill.py`
- [x] 4.2 Extend `db/system_prompts/water4energy.md` with a downscaling section and
      **explicit routing** between the two skills — evaluation/bias/ERA5/E3SM goes
      to the diagnostic, resolution/downscaling/Daymet goes to REFINE
- [x] 4.3 Confirm the existing `tools=["*", "!agenthpc_*"]` policy still fits and
      that the skill is picked up by the seed's skills loop with no registry edit

## 5. Hermetic tests

- [x] 5.1 `mcp_servers/vista_mcp_server/tests/test_refine_downscaling_job.py`:
      catalog entry validity, `cluster_defaults.json` parse + required env keys,
      `script_args` -> argv mapping per mode, the `--plots`/mode rejection, and the
      day guardrail. Markers: `unit` — **must stay out of** the `hpc`/`live` lanes
- [x] 5.2 `results.json` composition tested against captured fixtures of the three
      upstream JSON files (copied from `docs/reference_1990/`), including the
      hard-failure path when one is missing or malformed
- [x] 5.3 `backend/tests/test_refine_downscaling_skill.py`: SKILL.md frontmatter
      parses, the skill seeds onto the `water4energy` project alongside the
      diagnostic, seed snapshot updated, and the project tool policy is unchanged
- [x] 5.4 Lint and test green as CI runs them (ruff 0.15.7 per `.gitlab-ci.yml`,
      hermetic marker filter). Verified in a clean checkout: backend 393 passed,
      vista-mcp 140 passed. **Note:** `./scripts/ci-local.sh` fails in a working
      tree containing stray `__pycache__`-only dirs under `hpc_jobs/` or
      `db/skills/` — the catalog and seed scanners reject them

## 6. Frontier validation (manual — NOT in PR CI)

- [ ] 6.1 Submit `--mode infer` for one day; confirm GPU binding, wall time, output
      shape `1368x3096`, and the quicklook figures
- [ ] 6.2 Submit `--mode evaluate --max-days 1`, then one `--plots` run; confirm the
      spatial-statistics figures and the baseline comparison
- [ ] 6.3 Replace the placeholder `duration` with the measured value and fold the
      measured wall time, GPU name, and env state into README + SKILL.md
- [ ] 6.4 Confirm `$VISTA_OUT` retention behavior and tighten the day guardrail if
      bulk output is not garbage-collected (design open question 2)

## 7. Archive

- [ ] 7.1 `openspec validate refine-downscaling --type change` passes
- [ ] 7.2 Archive the change and merge the deltas into `openspec/specs/`
