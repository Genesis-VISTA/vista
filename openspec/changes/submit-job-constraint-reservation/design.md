## Context

`submit_hpc_job` (`mcp_servers/vista_mcp_server/src/vista_mcp_server/submit_job_mcp.py`)
validates `node_count` and `duration`, then dispatches to one function per
cluster. The constraint comes only from `cluster_defaults.json`, as
`IriAttributes.constraint` in `lib/iri.py`. Here is how each cluster sends it
today:

- **Odo and Frontier** send `custom_attributes: {"constraint": ...}` only when a
  constraint is set (`_submit_odo_job`, `_submit_frontier_job`).
- **Perlmutter** always sends it, even as `null`. Perlmutter also uses the value
  to decide whether to set `SLURM_GPUS_PER_NODE` (`_submit_perlmutter_job`).
- **Lux** renders `-C` in `lib/slurm_ssh.render_batch_script` and has no
  reservation support. Values are interpolated into `#SBATCH` lines unescaped.

The amscrot SDK accepts only these attribute keys: `duration`, `queue_name`,
`account`, `reservation_id` and `custom_attributes`. It drops any other key
silently, so a top-level `reservation` would never reach the wire. Existing
spec tests replace `IriClient` with `tests/fakes/iri.py`, so nothing exercises
the SDK's conversion.

The spike (see proposal.md, Why) established three facts about OLCF's status
response:

- It carries Slurm's own job record under `provider_status.meta_data.s3m`,
  including `constraints` (a string, `""` when none) and `reservation`
  (`{"id", "name"}`).
- VISTA's status tool discards this record today.
- The record was observed only on finished jobs.

## Goals / Non-Goals

**Goals:**
- One validation and resolution step for the two values, shared by every
  cluster and run before the dry-run branch.
- On Odo and Frontier, a check that starts passing without a VISTA change once
  OLCF forwards constraints.

**Non-Goals:**
- Moving Odo or Frontier to IRI v2. The constraint is lost below both versions.
- Showing applied values in `get_hpc_job_status`.
- Running the post-submit check on Perlmutter or Lux. Perlmutter's constraint
  reaches Slurm in practice, its record format is unknown, and Lux writes the
  script itself.
- Changing the HPC skill validator. Submit-time behaviour is the single source
  of truth, so a validator message can't go stale when OLCF changes.
- A live regression test kept in the repo. The live check is a one-off manual
  acceptance task.

## Decisions

**Validate once, at the top of `submit_hpc_job`.** The constraint must match
`^[A-Za-z0-9_.:,*&|\[\]()-]+$` (parentheses group features inside brackets)
and the reservation `^[A-Za-z0-9_.:,-]+$` (Slurm takes a comma-separated list).
Anything else raises a `ToolError` naming the argument and the allowed
characters. The check runs before `dry_run.record_submit`, so dry runs refuse
bad values too.
- *Rejected: a per-cluster allow-list of features.* VISTA would have to track
  each facility's features.
- *Rejected: rejecting only newlines.* That lets junk through to fail late at
  Slurm.

**Resolve the effective constraint in one place.** A small helper returns
`arg if arg is not None else defaults.iri.constraint`. Every per-cluster
function receives the effective value instead of reading
`defaults.iri.constraint`, and Perlmutter's GPU-env decision uses it too.
Combining with the default was rejected: the agent could never drop or change
the default, for example to run a `gpu` job on `cpu`. The tool description
tells the agent that the argument replaces the default and to restate the
default when it wants to keep it.

**IRI payload.** On Perlmutter, Odo and Frontier, send `reservation_id` and
`custom_attributes.constraint`, each only when set. This removes Perlmutter's
`{"constraint": None}`, which the SDK's `Dict[str, StrictStr]` would reject.
A new hermetic test runs the real
`IriServiceClient._convert_to_iri_job_spec(...).to_dict()` on a spec built by
VISTA. It asserts that both fields survive, which guards against the SDK's
silent drop of unknown keys. The test reaches a private SDK method; if an SDK
upgrade renames it, the test fails loudly. That beats it passing without
checking anything.

**Lux.** `render_batch_script` gains `reservation: str | None` and renders
`--reservation=<name>`. Values are safe there because validation already ran.

**Post-submit check, Odo and Frontier only.** It runs only when the effective
constraint or the reservation is set:

1. After `iri_client.submit_job`, call `get_job_status(job_id)` once.
2. Read `provider_status.meta_data.s3m` from the result.
3. A requested constraint counts as dropped when `constraints == ""`. Any
   non-empty constraint counts as passed: Slurm may normalise the expression,
   and nothing suggests a partial rewrite.
4. If the constraint was dropped, call `cancel_job(job_id)`. Then raise a
   `ToolError` saying OLCF did not pass the constraint to Slurm (job id
   included), and that Perlmutter and Lux take a constraint.
5. A reservation never cancels. OLCF is known to pass it: Slurm refuses an
   unknown one at submit, so a submit that succeeded reached Slurm with it. An
   empty `reservation.name` is therefore more likely a lagging or differently
   shaped record, and it only marks the reservation unverified.
6. If the status call raises, or the `s3m` record or its fields are missing,
   or the reservation is unverified, keep the job and add
   `verified: no (<reason>)` to the summary. Each value is judged on its own,
   so an unverified reservation never hides a dropped constraint.
- *Rejected: cancelling on an empty reservation name.* No live run has used a
  real reservation, so whether a just-queued record carries it is unknown, and
  a wrong cancel would break every reservation submit on OLCF.
- *Rejected: re-reading the status before cancelling.* It slows every
  reservation submit and still guesses at the lag.

The check lives in a helper that both `_submit_odo_job` and
`_submit_frontier_job` call, so the two stay identical.
- *Rejected: refusing statically.* It needs a VISTA release when OLCF fixes
  S3M.
- *Rejected: reporting the result in the summary only.* An unconstrained job
  would keep running unless the agent acted.
- *Rejected: cancelling when the check is indeterminate.* A status hiccup
  would kill good jobs.

**Summary lines.** Add `constraint: <value>` and `reservation: <value>` when
set, and `verified: ...` when the check could not be completed. They follow
the existing `key: value` form, so `agents/campaign/hpc_tools.parse_submit_summary`
keeps working. Absent keys stay empty.

**Backend pass-through.** The `HpcTools.submit` protocol (`subagent.py`) and
`McpHpcTools.submit` (`hpc_tools.py`) gain `constraint` and `reservation`. Each
is forwarded only when not `None`, the same pattern as `node_count`. The
forum's `simulation.py` is untouched; its submit call keeps relying on the
defaults, so its keyword arguments must stay compatible. The fake in
`tests/harness/fake_mcp.py` gains the two parameters.

A campaign supplies them the way it supplies `cluster`: the campaign agent's
`dispatch_cycle` tool takes optional `constraint` and `reservation`, and
`planner.dispatch_candidate` puts them on each `SubAgentOrder`, which
`SubAgent.dispatch` passes to `hpc.submit`. So they apply to one dispatched
cycle, every role's job in it, as the user asked for. A per-role setting in
the campaign manifest was rejected: a reservation belongs to an event, not to
a role.

## Risks / Trade-offs

- **[A just-queued job's Slurm record may lack `constraints`]** If so, every
  constrained Odo or Frontier submit is kept as "unverified" rather than
  cancelled. → Checked on Odo (task 8.2): a just-queued job's record already
  carries `constraints`, so the check cancels as intended.
- **[A short job finishes before the cancel lands]** The job runs unconstrained
  for its whole duration. → The `ToolError` is still raised. The cases that
  matter, such as NVMe or long jobs, queue long enough to cancel.
- **[One extra IRI status call per constrained OLCF submit]** → It's a single
  GET, and only when a constraint or reservation is set.
- **[A Frontier job with a default constraint now gets cancelled instead of
  running]** → No bundled job sets one. The `ToolError` names the cause, and
  dropping the constraint restores the old behaviour.
- **[The SDK conversion test is coupled to a private method]** → It fails
  loudly on rename. Swapping it for a local HTTP fake is the fallback.
- **[The SDK conversion test skips in CI]** amscrot is only in the private `hpc`
  extra, which CI and the dev venv don't install. → It runs wherever `hpc` is
  installed; accepted as is.

## Migration Plan

No data or configuration migration. Rollback is reverting the change. Jobs
without a constraint or reservation are unaffected.
