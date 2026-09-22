# refine-downscaling Specification

## ADDED Requirements

### Requirement: Single catalog entry with an explicit mode

The catalog SHALL expose exactly one `refine-downscaling` job whose behavior is
selected by a `--mode` argument in `script_args`. `--mode infer` SHALL be the
default. `--plots` SHALL be accepted only with `--mode evaluate`.

#### Scenario: Mode defaults to inference

- **WHEN** `script_args` is empty or omits `--mode`
- **THEN** the wrapper SHALL run the inference pipeline
- **AND** `results.json` SHALL record `"mode": "infer"`

#### Scenario: Plots outside evaluate are rejected

- **WHEN** `script_args` contains `--plots` without `--mode evaluate`
- **THEN** the wrapper SHALL exit non-zero before submitting any work
- **AND** the message SHALL name the incompatible combination

### Requirement: Read-only pre-staged assets with preflight

The job SHALL read code, data, checkpoint, and Python environment from the
pre-staged Frontier locations named in `cluster_defaults.json`, SHALL write only
inside `$VISTA_OUT`, and SHALL verify every required path before doing work.

#### Scenario: Missing asset fails with the exact path

- **WHEN** a required demo root, data dir, checkpoint, or environment path is absent
- **THEN** the job SHALL exit non-zero during preflight
- **AND** stderr SHALL name the exact missing path
- **AND** no pipeline SHALL be invoked

#### Scenario: Inputs are never modified

- **WHEN** any mode runs to completion
- **THEN** no file under the demo root SHALL be created, modified, or deleted

#### Scenario: Missing environment is not rebuilt

- **WHEN** the configured conda environment is absent or torch fails to import
- **THEN** the job SHALL fail with an actionable message naming the environment path
- **AND** it MUST NOT attempt to build or install an environment

### Requirement: Structured results composed from upstream JSON

The wrapper SHALL build `results.json` (schema
`vista/refine-downscaling/results/v1`) from the JSON files the upstream pipelines
write, and MUST NOT parse metrics out of stdout. A missing or unparseable upstream
JSON SHALL be a hard failure.

#### Scenario: Evaluate carries model and baseline metrics

- **WHEN** `--mode evaluate` completes
- **THEN** `results.json` SHALL carry per-variable `bias`, `mae`, and `rmse` for both
  the model and the bilinear baseline, plus `mae_improvement_percent` and `count`
- **AND** each variable SHALL carry its units

#### Scenario: Unparseable upstream output fails loudly

- **WHEN** an expected upstream JSON file is missing or cannot be parsed
- **THEN** the job SHALL exit non-zero
- **AND** it MUST NOT emit a `results.json` containing null metrics

#### Scenario: Provenance is recorded

- **WHEN** any mode completes
- **THEN** `results.json` SHALL record the checkpoint path and SHA256, the
  environment path, the resolved pipeline arguments, wall time, and the Slurm job id

### Requirement: Bounded output volume

The wrapper SHALL default to one day, SHALL refuse more than 31 days unless
`--allow-large` is given, and SHALL report the path and size of bulk artifacts
rather than moving them into the fetchable set by default.

#### Scenario: Oversized request is refused

- **WHEN** `--days` exceeds 31 and `--allow-large` is absent
- **THEN** the wrapper SHALL exit non-zero before running a pipeline
- **AND** the message SHALL state the projected output size

#### Scenario: Bulk artifacts are reported, not fetched

- **WHEN** a run writes NetCDF output or retained predictions
- **THEN** `results.json` SHALL record their `$VISTA_OUT` paths and byte sizes

### Requirement: Every run yields a displayable figure

Each mode SHALL produce at least one PNG in `$VISTA_OUT` so the agent has something
to `display_file` alongside the metrics. Figure rendering is presentation only and
SHALL NOT compute any metric, and a rendering failure SHALL NOT discard an
otherwise successful pipeline run.

#### Scenario: Inference produces a quicklook

- **WHEN** `--mode infer` completes
- **THEN** the wrapper SHALL write one coarse-input vs downscaled-output PNG per
  variable for the first timestep, on a shared color scale
- **AND** `results.json` SHALL list those figures

#### Scenario: Evaluation without plots still has a figure

- **WHEN** `--mode evaluate` completes without `--plots`
- **THEN** the wrapper SHALL write a model vs bilinear-baseline comparison chart
  rendered from `evaluation_summary.json`

#### Scenario: A rendering failure degrades instead of failing the run

- **WHEN** plotting dependencies are unavailable, or figure rendering raises after
  the pipeline has already succeeded
- **THEN** the job SHALL still exit zero and write `results.json`
- **AND** `results.json` SHALL carry an explicit `warnings` entry naming the cause

### Requirement: Skill guidance and project wiring

A `refine-downscaling` skill SHALL be registered and attached to the seeded
`water4energy` project alongside `water4energy-diagnostic`, and its SKILL.md SHALL
carry the interpretation guardrails.

#### Scenario: Skill is seeded onto the water4energy project

- **WHEN** the database is seeded offline
- **THEN** the `water4energy` project's skills SHALL include both
  `water4energy-diagnostic` and `refine-downscaling`

#### Scenario: SKILL.md distinguishes a smoke test from the reference

- **WHEN** SKILL.md is read
- **THEN** it SHALL state that a short-interval run is not the full-year 1990
  reference evaluation
- **AND** it SHALL state that training and new variables, regions, or grids are out
  of scope
