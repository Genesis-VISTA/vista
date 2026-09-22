## Context

`Water4Energy_AgenticDemo` (REFINE) is a complete super-resolution package: a
`refine_downscaling/` library, four numbered pipeline scripts, two plotting
utilities, Slurm launchers, a skill-authoring kit, and a pretrained 6x checkpoint
(63 MB, SHA256 `2e37007658d2...`). It downscales `tmin`/`tmax`/`prcp` from
228x516 to 1368x3096 on the Daymet grid.

The critical operational fact: **a git clone is not the demo.** `daymet/data/`,
`daymet/prepared/shared/` and `checkpoints/*.pt` are gitignored, and the remote is
SSH-only (`git@github.com:daliwang/Water4Energy_AgenticDemo`). The complete package
— code, 1980-1990 inputs, prepared Stage 2 index, DEMs, normalization, weights, and
the 1990 reference diagnostics — already lives at
`/lustre/orion/world-shared/cli138/haoran/GM_Downscaling_demo1`, with a matching
ROCm/PyTorch conda env at `/lustre/orion/world-shared/cli138/haoran/envs/torch_rocm`.

VISTA submits Frontier jobs under the shared `chm243` account
(`config.py: frontier_account`) as a service identity, so **`world-shared` is what
makes this work at all**: the demo's own launchers use `-A cli138` and a
`proj-shared/cli138` env, neither of which VISTA can use.

## Goals / Non-Goals

**Goals:**

- Run the demo pipelines unmodified on Frontier GPUs via one `submit_hpc_job`
- Emit one structured `results.json` per run, composed from JSON the pipelines
  already write
- Keep the agent from starting an unbounded run or dragging 18 GB into the sandbox
- Keep PR CI hermetic — no Frontier, no Lustre, no GPU, no NetCDF

**Non-Goals:**

- Training / data prep, new grids or variables, vendoring, campaigns, VISTAGuard

## Decisions

1. **Run in place from `world-shared`; clone nothing, vendor nothing, build nothing.**
   The job sets `PYTHONPATH` to the demo root and calls the pipelines with absolute
   `--data-dir` / `--checkpoint` / `--input` paths, writing exclusively to
   `$VISTA_OUT`. Root, env, and checkpoint are `cluster_defaults.json` env vars
   (`REFINE_BASE_DIR`, `REFINE_ENV`, `REFINE_CHECKPOINT`) so a move is a config edit.
   Rationale: the clone-at-runtime pattern of `salt-chemistry-md` and
   `water4energy-diagnostic` cannot work here — the assets that matter are not in
   the repo, and the remote needs a key. Vendoring would fork the science code.

2. **Depend on the pre-provisioned conda env; do not self-heal.**
   `module load PrgEnv-gnu/8.6.0 rocm/6.4.1 craype-accel-amd-gfx90a miniforge3` then
   `conda activate $REFINE_ENV`. If the env is missing or torch fails to import, the
   job fails with one actionable line naming the path.
   Rationale: the opposite of `water4energy-diagnostic` decision 2, and deliberately
   so. There the fallback was ~3 minutes of manylinux wheels; here it would be a
   multi-GB ROCm PyTorch build inside a GPU job's walltime — a support ticket, not a
   retry. A missing env must be a loud config failure, never a silent rebuild.

3. **One job, two modes.** `--mode infer` (default) and `--mode evaluate`, with
   `--plots` valid only for `evaluate`. One catalog entry, one README, one
   `results.json` schema with a `mode` discriminator.
   Rationale: three catalog entries would triple the contract surface for what is
   one package, one env, and one checkpoint; and the agent picks a mode far more
   reliably than it picks among three near-identically-described jobs.

4. **Compose `results.json` from upstream JSON; never scrape stdout.**
   `pipeline_04_infer.py` writes `<output>.nc.json`; `pipeline_03_evaluate.py`
   writes `evaluation_summary.json` (model *and* bilinear-baseline bias/MAE/RMSE per
   variable, plus `mae_improvement_percent` and `count`);
   `utility_plot_spatial_statistics.py` writes `spatial_statistics_index.json`. The
   wrapper reads those and adds provenance (checkpoint SHA256 verified against the
   registry, demo-root revision if present, env path, torch/ROCm versions, GPU name,
   wall time, Slurm job id).
   Rationale: this is the single biggest improvement over `water4energy-diagnostic`,
   whose metrics came from a regex over printed prose. A missing or unparseable
   upstream JSON is a hard failure, never a `results.json` of nulls.

5. **Derived products travel; bulk output stays.**
   `results.json` and PNGs are the fetchable surface. Inference NetCDF is ~51 MB/day
   (~18.5 GB/year) and retained predictions are ~18 GB, so `results.json` reports
   their `$VISTA_OUT` paths and sizes and the SKILL.md tells the agent to fetch them
   only on explicit request, after stating the size.

6. **Day-count guardrail.** `--days` defaults to 1 and the wrapper refuses more than
   31 without `--allow-large`, naming the projected output size.
   Rationale: `--end-index` is zero-based and exclusive, a full year is one typo
   away, and the agent is the one typing. The guardrail is in the wrapper, not the
   SKILL.md, because prose does not enforce.

7. **Quicklook figure for `infer`, written by the wrapper.**
   Upstream plots only evaluation spatial statistics, which need truth; a bare
   inference run would return numbers with nothing to `display_file`. The wrapper
   renders one coarse-input vs downscaled-output panel per variable for the first
   timestep. This is the only new science-adjacent code in the change, and it is
   presentation only — it computes no metric.

8. **GPU shape.** 1 node, `exclusive_node_use: true`. Frontier's prolog binds all 8
   GCDs and the pipelines use one; `--amp` and `--enforce-temperature-order` are on,
   matching the demo's own launcher. `MIOPEN_USER_DB_PATH` goes under `$VISTA_OUT`
   so the MIOpen cache never collides between jobs.

9. **`#SBATCH` headers are inert** — same as `water4energy-diagnostic` decision 3.
   Walltime is `frontier.duration`, queue is `frontier.iri.queue_name`. No shared
   dispatch code changes.

## Risks / Trade-offs

- **The whole change rests on one directory staying put and world-readable.**
  Decision 1 concentrates that risk deliberately: preflight stats every required
  path up front and fails with the exact path, so a move is a one-line config fix
  rather than a torch traceback.
- **A bad or updated `torch_rocm` env breaks every run with no fallback**
  (decision 2). Accepted: the failure is immediate, named, and a human fix.
- **Wall time is unmeasured.** The demo's launcher asks 30 min for one day on one
  GPU, which is a ceiling, not a measurement. `duration` starts at 1800 s and the
  manual Frontier run in task group 6 replaces it with a measured value.
- **Reference metrics cannot be regression-tested in CI** — they need the GPU, the
  checkpoint, and 365 days of truth. The registry values are pinned in SKILL.md as
  a *judgment* aid, and only the manual run can confirm them.
- **A one-day smoke test is not the full-year reference.** This is the change's
  headline interpretation trap; SKILL.md must say so where the agent cannot miss it.
- **`--plots` needs retained predictions**, so it is bounded by the same day
  guardrail and documented as the expensive path.

## Migration Plan

Additive throughout: no existing skill, job, project, or dispatch code changes
behavior. The `water4energy` project gains a second skill, so its system prompt
must route between evaluation (diagnostic) and downscaling (REFINE) rather than
assuming one workflow. Order: HPC job -> wrapper/metrics -> SKILL.md -> wiring ->
hermetic tests -> manual Frontier validation -> archive.

## Open Questions

- Measured wall time per day of inference, and therefore the right default
  `duration` — only the task group 6 run answers this.
- Whether `$VISTA_OUT` is garbage-collected. If it is not, retained NetCDF
  accumulates on Lustre and the guardrail in decision 6 may need to tighten.
