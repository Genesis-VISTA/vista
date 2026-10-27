# Tasks

## 1. Arguments and validation

- [x] 1.1 Add optional `constraint: str | None` and `reservation: str | None` to `submit_hpc_job`. Validate them against the design's two patterns before `resolve_job` side effects and before the dry-run branch, raising `ToolError` with the argument name and the allowed characters. Verify with new tests in `tests/test_submit_job_spec.py`:
  - `nvme\n#SBATCH -A x` and `res 1` are refused, and no client is called.
  - In dry-run mode, a bad value records no job (`tests/test_dry_run.py`).
- [x] 1.2 Add the effective-constraint helper (the argument if given, else `defaults.iri.constraint`) and pass the effective constraint and the reservation into all four `_submit_*_job` functions. Verify the existing spec, Lux, skill-job, forge-pretrain and job-account tests still pass.
- [x] 1.3 Add `constraint` and `reservation` to the tool description's Args block:
  - the constraint replaces the job's default, and the agent restates the default to keep it, e.g. `gpu&hbm80g`
  - which clusters take each argument
  - that Odo and Frontier verify both after submitting

  Verify with an assertion in `tests/test_skill_jobs.py::test_the_tool_description_mentions_skill_jobs`, or a sibling test.

## 2. Lux

- [x] 2.1 Add `reservation` to `slurm_ssh.render_batch_script`, rendering `--reservation=<name>`. Verify by extending `test_render_batch_script_optional_directives`.
- [x] 2.2 Pass the effective constraint and the reservation from `_submit_lux_job`, and add both to the `_lux_conn(ctx, "submit_hpc_job", ...)` prompt arguments. Verify that a `test_lux_submit.py` test submitting with `constraint="nvme", reservation="train1"` finds `#SBATCH -C nvme` and `#SBATCH --reservation=train1`.

## 3. IRI clusters

- [x] 3.1 On Perlmutter, Odo and Frontier, send `reservation_id` and `custom_attributes.constraint` only when set. Perlmutter's `SLURM_GPUS_PER_NODE` decision uses the effective constraint. Verify with `test_submit_job_spec.py` tests that read `iri.submitted[0][0]["attributes"]`:
  - each cluster carries `reservation_id` and `custom_attributes`
  - Perlmutter with no constraint has no `custom_attributes`
  - a `gpu` job given `cpu` has no `SLURM_GPUS_PER_NODE`
- [x] 3.2 Add a hermetic SDK conversion test. It builds an amscrot `JobSpec` from a VISTA Odo spec carrying both values, runs `IriServiceClient._convert_to_iri_job_spec(...).to_dict()`, and asserts `attributes.reservation_id` and `attributes.custom_attributes.constraint`. Verify that it passes, and that it fails if the spec puts `reservation` at the top level.

## 4. Odo and Frontier post-submit check

- [x] 4.1 Let `tests/fakes/iri.py` return a configurable `provider_status.meta_data.s3m` record per job, and let it raise from `get_job_status`. Verify the existing fake-IRI tests are unchanged.
- [x] 4.2 Add the shared check helper and call it from `_submit_odo_job` and `_submit_frontier_job`, only when a constraint or a reservation is set. It cancels and raises `ToolError` when either was dropped, and returns an "unverified" note when the check is indeterminate. Verify with tests covering:
  - an empty constraint record: cancelled, with an error naming OLCF, Perlmutter and Lux
  - an empty reservation name: cancelled
  - a non-empty constraint: kept
  - a status error: kept and unverified
  - missing `s3m` fields: kept and unverified
  - no constraint and no reservation: no status call
  - an Odo default constraint with no argument: still checked

## 5. Summary

- [x] 5.1 Add `constraint:`, `reservation:` and `verified:` lines to the submit summary when they apply. Verify with a spec test, and that `backend` `parse_submit_summary` still reads `job_id` and `cluster` from the new summary (an existing campaign test, or a new one).

## 6. Backend pass-through

- [x] 6.1 Add `constraint` and `reservation` to the `HpcTools.submit` protocol and `McpHpcTools.submit`, forwarding each only when not `None`. Update `tests/harness/fake_mcp.py`. Leave `agents/forum/simulation.py` unchanged. Verify with a backend test that `McpHpcTools.submit(..., reservation="r")` passes `reservation` and omits it when `None`. Run `./scripts/ci-local.sh backend test`.
- [x] 6.2 Let a campaign supply both, as it supplies `cluster`: add optional `constraint` and `reservation` to the campaign agent's `dispatch_cycle` tool (`agents/campaign/agent_tools.py`), to `CampaignPlanner.dispatch_candidate` and to `SubAgentOrder`, and have `SubAgent.dispatch` pass them to `hpc.submit`. Mention them in `dispatch_cycle`'s docstring. Verify with a backend test that dispatching a cycle with `reservation="train1"` submits every role's job with it, and that a cycle without one sends neither.

## 7. Docs and prompts

- [x] 7.1 Document both arguments in `docs/hpc-jobs.md`: that the constraint replaces the default, and the per-cluster mapping and checking. Add `-C` → `iri.constraint` / the `constraint` argument and `--reservation` → the `reservation` argument to `hpc-skill-author/SKILL.md`'s `#SBATCH` mapping. Verify with the docs link checker.
- [x] 7.2 Correct `backend/src/vista_backend/db/system_prompts/molten-salt.md:8` to the real signature: `submit_hpc_job(job, cluster?, node_count?, duration?, script_args?, constraint?, reservation?)`. Verify by reading the prompt, and with any bundled-prompt test.

## 8. Acceptance

- [x] 8.1 Run `./scripts/ci-local.sh mcp` and `./scripts/ci-local.sh backend`, lint and test, and verify both pass.
- [ ] 8.2 Manual, before merge, through VISTA's own `submit_hpc_job` on Odo with Sam's tokens:
  - `example` with `constraint="nvme"`: expect a `ToolError` naming OLCF and a cancelled job, which also confirms a just-queued job's Slurm record has `constraints`. If the result is "unverified" instead, record it and raise the follow-up in design.md's Risks.
  - `example` with a fake reservation: expect Slurm's "Requested reservation is invalid".

  Record both results in this file.
