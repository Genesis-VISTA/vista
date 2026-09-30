## ADDED Requirements

### Requirement: Declarative candidate argument encoding

A campaign manifest's subagent spec SHALL be able to declare how a candidate is
encoded into `script_args` for its bound HPC job, via an `args` block carrying an
`encoding` (`flags` or `json`), a `map` from candidate variable name to CLI flag,
and a fixed `extra` argument string. `CampaignPlanner.dispatch_candidate` SHALL
render `script_args` from that declaration rather than always serializing JSON.

#### Scenario: Flags encoding renders mapped variables in manifest order

- **WHEN** a subagent declares `encoding: flags` with a `map` of two variables
- **THEN** `dispatch_candidate` SHALL submit `script_args` containing each mapped
  variable as `<flag> <value>`
- **AND** the flags SHALL appear in the manifest's variable declaration order
- **AND** candidate variables absent from the `map` MUST NOT appear in `script_args`

#### Scenario: Extra arguments are appended

- **WHEN** a subagent declares a non-empty `args.extra` string
- **THEN** the rendered `script_args` SHALL include it in addition to the mapped flags

#### Scenario: JSON encoding remains available

- **WHEN** a subagent declares `encoding: json`
- **THEN** `dispatch_candidate` SHALL submit the candidate serialized as JSON

#### Scenario: Roles receive different argument subsets

- **WHEN** one candidate is dispatched to two roles with different `map` entries
- **THEN** each role's `script_args` SHALL contain only that role's mapped variables

#### Scenario: Manifests without an args block are unchanged

- **WHEN** `load_manifest` reads a `campaign.yaml` whose subagents declare no `args` block
- **THEN** it SHALL validate successfully
- **AND** the subagent SHALL dispatch with the candidate serialized as JSON, exactly as before this change

### Requirement: Declarative collect output files

A campaign manifest's subagent spec SHALL be able to declare the job output files
its result parser needs, via `collect_files`. The monitor's collector SHALL pass
those files through to `SubAgent.collect` so the parser receives real job outputs
rather than status text alone.

#### Scenario: Collector fetches declared files

- **WHEN** a finished job's role declares `collect_files: [results.json]`
- **THEN** the collector SHALL call the HPC `fetch_outputs` boundary with that file list
- **AND** the parser SHALL receive the fetched content as `raw_outputs`

#### Scenario: No declared files leaves outputs empty

- **WHEN** a finished job's role declares no `collect_files`
- **THEN** the collector SHALL NOT call `fetch_outputs`
- **AND** the parser SHALL receive an empty `raw_outputs`, as today

#### Scenario: Per-role file lists are independent

- **WHEN** two roles declare different `collect_files`
- **THEN** each job SHALL be collected with only its own role's file list

### Requirement: Strict backward compatibility

This change SHALL be strictly additive. A campaign manifest that declares neither
`args` nor `collect_files` SHALL dispatch and collect exactly as it does today, so
no shipped campaign changes behavior without opting in.

#### Scenario: Existing manifests load unchanged

- **WHEN** every `campaign.yaml` currently under `backend/src/vista_backend/db/skills/` is loaded
- **THEN** `load_manifest` SHALL validate each one without modification

#### Scenario: Non-adopting manifests dispatch identically

- **WHEN** a candidate is dispatched through a manifest with no `args` block
- **THEN** the submitted `script_args` SHALL be byte-identical to the pre-change output

#### Scenario: Non-adopting manifests collect identically

- **WHEN** a job is collected for a role with no `collect_files`
- **THEN** the HPC `fetch_outputs` boundary MUST NOT be called
- **AND** the parser SHALL receive an empty `raw_outputs`

#### Scenario: SPLASH is not modified

- **WHEN** this change is applied
- **THEN** `backend/src/vista_backend/db/skills/splash-planner/` and the SPLASH job
  wrappers under `hpc_jobs/` SHALL be unchanged

### Requirement: Hermetic coverage

Tests for this capability SHALL run in PR CI without a cluster, LLM, or network,
under the default marker filter `not live and not hpc and not sandbox`.

#### Scenario: Suite runs with no credentials

- **WHEN** `./scripts/ci-local.sh backend test` runs with no HPC or model credentials
- **THEN** the encoding and collect-file tests SHALL run and pass
- **AND** they MUST NOT be marked `live`, `hpc`, or `sandbox`
