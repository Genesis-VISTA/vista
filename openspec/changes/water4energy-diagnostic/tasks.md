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

- [x] 3.1 Create `backend/src/vista_backend/db/skills/water4energy-diagnostic/SKILL.md`
      with valid frontmatter (kebab-case `name`, trigger-rich `description`,
      `metadata.version`/`tags`, `author`). `license` is deliberately omitted:
      the upstream repo ships no LICENSE and states no terms, so asserting one
      would be a fabrication
- [x] 3.2 Document the workflow: submit → poll → `get_hpc_job_outputs` →
      `display_file` each figure → interpret from `results.json`
- [x] 3.3 Document the `script_args` contract (`--resolution`, `--dpi`,
      `--checksum-inputs`) plus the "never compare across resolutions" rule
- [x] 3.4 Write the interpretation section: reference values for a healthy run,
      why TVA precipitation `r ≈ 0.53` is expected (20 grid cells + interpolation
      smoothing), why temperature nRMSE is omitted (Celsius/Kelvin zero-point),
      and the "small differences normal, large differences not" rule
- [x] 3.5 Write guardrails: never report figures without the metrics that back
      them; cite `job_id`; flag rather than hide a failed preflight; state the
      cold-run `duration="00:30:00"` guidance; never invent the figure-only
      metrics; do not call the TVA service area a watershed

## 4. Wiring

- [x] 4.1 Add `backend/src/vista_backend/db/system_prompts/water4energy.md`
- [x] 4.2 Add the `water4energy` project to `db/seed.py` with a fixed UUID
      (`e0468a13-50ae-41e3-a8f9-e461b4b4bc3c`), `skills: ["water4energy-diagnostic"]`,
      and `tools=["*", "!agenthpc_*"]` — `rag_search` is denied automatically
      because the project has no knowledge bases
- [x] 4.3 Confirm the skill is picked up by the `SKILLS_SRC` loop with no
      `SKILL_ASSETS` entry (no vista-data assets needed → never `skipped_skills`)
- [x] 4.4 Campaign tools (`start_campaign` … `finish_campaign`) are registered
      directly on the agent by `register_campaign_tools`, so `project.tools`
      cannot gate them. The system prompt tells the agent this project runs no
      campaign; do not attempt to filter them via patterns

## 5. Offline tests

- [x] 5.1 `backend/tests/test_water4energy_skill.py` (54 tests): `SKILL.md` parses via
      `read_skill`, frontmatter + trigger-term + required-section assertions,
      serializer round-trip, and pins on the interpretation guidance, the polling
      cadence, and the figure-only-metrics caveat
- [x] 5.2 `mcp_servers/vista_mcp_server/tests/test_water4energy_job.py` (19 tests):
      `cluster_defaults.json` validates as `ClusterDefaults`, Frontier-only,
      `duration == 600`, `node_count == 1`, 1-rank, `queue_name == "batch"`, declared
      env keys, and no `#SBATCH` directives. Lives on the MCP side because
      `ClusterDefaults` is not importable from the backend venv
- [x] 5.3 `script_args` → wrapper argv mapping, absolute-path resolution, and both
      preflight failure messages (unreadable data dir, missing climatology)
- [x] 5.4 stdout → `results.json` against the verified Frontier summary as the
      fixture, plus three loud-failure cases and the provenance/checksum paths
- [x] 5.5 Seed snapshot for the `water4energy` project (stable id, skills, tools,
      usage limits, system prompt) + agent-prompt wiring tests carried over from the
      step-4 probe, including the campaign-tools arrangement from task 4.4
- [x] 5.6 `./scripts/ci-local.sh` green: backend 332, vista-mcp 90, dev-mcp 36,
      UI lint clean (2 pre-existing `<img>` warnings)
- [x] 5.7 No `live` / `hpc` / `sandbox` markers added; both files verified to run
      under the CI hermetic filter
- [x] 5.8 Mutation-checked the three assertions that matter most: removing the poll
      cadence, dropping `uri_map`, and silencing the partial-parse failure each turn
      the suite red

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
