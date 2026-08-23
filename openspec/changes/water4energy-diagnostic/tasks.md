## 1. HPC job package

- [ ] 1.1 Create `hpc_jobs/water4energy-diagnostic/README.md` (must start with
      `# water4energy-diagnostic` — catalog contract) documenting the `script_args`
      contract, outputs, and the pre-staged-input prerequisite
- [ ] 1.2 Add `cluster_defaults.json`: `frontier` only — `duration: 600`,
      `node_count: 1`, `queue_name: "batch"`, and `iri.environment` carrying
      `W4E_DATA_DIR`, `W4E_ENV`, `W4E_REPO_URL`, `W4E_REPO_REF`
- [ ] 1.3 Add `job.frontier.slurm`: require `VISTA_OUT` / `RUN_DIR_Frontier` /
      `W4E_DATA_DIR`, clone the repo, ensure the venv (`flock`-guarded,
      import-verified, rebuild on failure), export `MPLBACKEND=Agg` +
      `MPLCONFIGDIR`, then `srun -N1 -n1` the wrapper with `"$@"`
- [ ] 1.4 Add `run_diagnostic.py` wrapper: preflight the three inputs with an
      actionable error, invoke `plot_e3sm_era5.py` with `--cartopy-data`, copy the
      four figures into `$VISTA_OUT`, propagate a nonzero exit
- [ ] 1.5 Confirm no `#SBATCH` directives are relied on (they are inert — design
      decision 3) and that a cold vs warm environment is reported on stdout

## 2. Structured metrics

- [x] 2.1 Capture the real Frontier stdout as a test fixture (lands in task group 5
      with the parser tests; the format is pinned from `FRONTIER_TEST_REPORT.md` and
      upstream's print statements)
- [x] 2.2 Implement stdout → `results.json` parsing in `run_diagnostic.py`;
      fail loudly on an unmatched metric rather than emitting nulls
- [x] 2.3 Define the `results.json` schema: per-variable `units` plus global and
      regional `correlation` / `rmse` / `bias` (all the printed summary carries —
      see design decision 5), plus provenance (repo SHA, input paths + optional
      checksums, resolution, package versions, wall time, hostname, Slurm job id)
- [x] 2.4 Document the schema in the job README, including the figure-only metrics
      limitation and what a future upstream `--json-out` would add

## 3. SKILL.md

- [ ] 3.1 Create `backend/src/vista_backend/db/skills/water4energy-diagnostic/SKILL.md`
      with valid frontmatter (kebab-case `name`, trigger-rich `description`,
      `metadata.version`/`tags`, `license`, `author`)
- [ ] 3.2 Document the workflow: submit → poll → `get_hpc_job_outputs` →
      `display_file` each figure → interpret from `results.json`
- [ ] 3.3 Document the `script_args` contract (`--resolution`, `--dpi`, and the
      pass-through shape that a future generalization patch will extend)
- [ ] 3.4 Write the interpretation section: reference values for a healthy run,
      why TVA precipitation `r ≈ 0.53` is expected (20 grid cells + interpolation
      smoothing), why temperature nRMSE is omitted (Celsius/Kelvin zero-point),
      and the "small differences normal, large differences not" rule
- [ ] 3.5 Write guardrails: never report figures without the metrics that back
      them; cite `job_id`; flag rather than hide a failed preflight; state the
      cold-run `duration="00:30:00"` guidance

## 4. Wiring

- [ ] 4.1 Add `backend/src/vista_backend/db/system_prompts/water4energy.md`
- [ ] 4.2 Add the `water4energy` project to `db/seed.py` with a fixed UUID,
      `skills: ["water4energy-diagnostic"]`, and a tool allowlist that keeps the
      HPC toolchain and excludes `agenthpc_*`
- [ ] 4.3 Confirm the skill is picked up by the `SKILLS_SRC` loop with no
      `SKILL_ASSETS` entry (no vista-data assets needed → never `skipped_skills`)

## 5. Offline tests

- [ ] 5.1 `backend/tests/test_water4energy_skill.py`: `SKILL.md` parses via
      `read_skill`, name/description/frontmatter assertions, required sections present
- [ ] 5.2 Assert `cluster_defaults.json` validates as `ClusterDefaults`, is
      Frontier-only, `duration == 600`, `node_count == 1`
- [ ] 5.3 Test `script_args` → wrapper argv mapping and the preflight failure message
- [ ] 5.4 Test stdout → `results.json` parsing against the task-2.1 fixture,
      including the loud-failure path on malformed input
- [ ] 5.5 Extend the seed snapshot test for the `water4energy` project
- [ ] 5.6 Confirm the existing `hpc_jobs/` catalog contract tests pass over the new
      entry, and `./scripts/ci-local.sh` is green
- [ ] 5.7 No `live` / `hpc` / `sandbox` marked tests added — nothing in this change
      may require Frontier, Globus, or an S3M token to merge

## 6. Manual Frontier validation (out of CI)

- [ ] 6.1 Submit via `submit_hpc_job(job="water4energy-diagnostic", cluster="frontier")`
      with `duration="00:30:00"` for the cold-env run
- [ ] 6.2 Diff `results.json` against the reference values
      (temperature global `r=0.9937`, RMSE `1.685 °C`, bias `+0.324 °C`;
      precipitation global `r=0.8879`, RMSE `1.047 mm/day`, bias `+0.083 mm/day`)
- [ ] 6.3 Confirm a warm-env resubmission fits inside the 600 s default
- [ ] 6.4 Record the `batch` queue wait; if it dominates the ~40 s runtime, file a
      separate change for a `qos` passthrough (out of scope here)
- [ ] 6.5 Verify the four figures render through `display_file` in the UI
- [ ] 6.6 Fold anything learned back into `SKILL.md` and the job README

## 7. Archive

- [ ] 7.1 Sync the `water4energy-diagnostic` capability into `openspec/specs/`
- [ ] 7.2 Fold the catalog delta into `openspec/specs/hpc-job-contracts/spec.md`
- [ ] 7.3 Archive the change
