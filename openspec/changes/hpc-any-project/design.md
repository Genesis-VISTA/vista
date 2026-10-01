# Design

## Context

See proposal.md for why. Today, in `mcp_servers/vista_mcp_server`:

- `config.py` fixes `odo_account`/`frontier_account`/`lux_account` and the
  matching `*_remote_dir`. `_require_olcf_access` calls `require_s3m_project`
  before every Odo/Frontier file operation, and the Slurm account comes from
  the same settings.
- `UserConfig.require_globus_token` falls back to
  `settings.globus_tokens(cluster)`, the deployment's env-var pair. That
  fallback is the only reason the project check existed (see its docstring in
  `lib/olcf_token.py`).
- Frontier, Perlmutter and Lux put jobs under `<base>/<settings.session_id>/out`.
  `session_id` is random for each server run, so the rendered paths are kept in
  `_submitted_jobs`, which is persisted to `data/hpc_job_registry.json`. Odo
  dropped the session folder in `c9a0baf8` and uses a pre-made group-writable
  `<base>/out`.
- Frontier creates `<session>/out` through Globus before submitting. That folder
  belongs to the researcher, so the IRI automation user can write to it only
  because of a manual default ACL on `chm243/proj-shared/vista`.

In the backend, `HpcClusterSettings` copies the account and Globus settings,
and `services/hpc_status.py` produces `wrong_project` and
`identity: "deployment"`. The user table already has a `frontier_remote_dir`
column that nothing reads. `_add_missing_columns` in `db/db.py` adds new
nullable columns on startup.

## Goals / Non-Goals

**Goals:**
- Work out every job path from `(remote_dir, job, job_id)`, with no state kept
  between calls.
- Use one code path for the layout and the folder checks, shared by all
  clusters where they behave the same.

**Non-Goals:**
- Showing a missing remote directory on the HPC cards. Submission reports it;
  adding a card state would change the availability spec further than needed.
- Dropping database columns. `remote_hpc_jobs_dir` and `frontier_account` stay
  in `UserTable`, as the legacy `s3m_token` does, but leave every API schema.
- Migrating old jobs under `<session_id>/` folders.

## Decisions

**1. The Slurm account is the token's introspected project.**
`_require_olcf_access` becomes `_olcf_project(cfg, cluster) -> (token, project)`.
It is called only at submit time, which is the only place the account is
needed. Status, outputs and cancel make no introspection call. They rely on IRI
and the researcher's Globus identity, which the facility authorizes.
`require_s3m_project` is deleted; `get_s3m_token_project` and its cache stay.
*Alternative:* a per-user account setting. Rejected: the token can only
authorize its own project's automation user, so a setting could only disagree
with it.

**2. Globus comes only from the researcher.** `require_globus_token` keeps its
two user sources and loses the `settings.globus_tokens` fallback.
`AppSettings.{odo,frontier}_globus_*refresh_token` and `globus_tokens()` are
deleted, and so are their `HpcClusterSettings` copies. `_globus` in
`hpc_status.py` mirrors the same two sources. `Check.identity` is removed,
since it can only ever be "own". `scripts/get_globus_token.py` loses
`--save-env`, and its default mode, which prints the tokens, stays for
debugging.

**3. Remote folders are user settings.** `UserTable` gains `odo_remote_dir`
and `lux_remote_dir`; `frontier_remote_dir` is reused. All three are added to
`_USER_CONFIG_NULLABLE_FIELDS` and the create, update, self-update and public
schemas. `remote_hpc_jobs_dir` and `frontier_account` are removed from those
schemas. `UserConfig` (MCP) gains the three fields and
`require_remote_dir(cluster)`, which raises a `ToolError` naming the setting.
Perlmutter's existing `nersc_remote_dir` check moves into the same helper.
`_submit_lux_job` starts taking `cfg`.

**4. One path helper.**
```
remote_paths(remote_dir, job) -> base, src=<base>/<job>/src, out=<base>/out
stdout = <out>/log-%j.out   stderr = <out>/log-%j.err   VISTA_OUT = <out>/$SLURM_JOB_ID
```
Submit uses the `%j` templates. Status and outputs substitute the job id. The
IRI `directory` / Lux `workdir` is `<base>`; Odo's setup snippet still does
`cd <src>`. `session_id` is deleted from `AppSettings`. The metrics
`session_id` is a different field and is unaffected. `IriDefaults.account` and
`IriDefaults.remote_dir` are deleted. `IriDefaults` ignores unknown keys, so an
old `cluster_defaults.json` still loads.

**5. Nothing the job writes is made through Globus.** Frontier stops calling
`operation_mkdir_p` for the out folder. Slurm creates `out/`, and the job
prefix runs `mkdir -p -m 2775 "$VISTA_OUT"` on every cluster, so outputs in a
shared folder stay group-writable. Sources are still uploaded through Globus
under `<base>/<job>/src`, which the automation user only reads. Perlmutter and
Lux run as the researcher, so their existing `mkdir` of `out/` is harmless, but
it is dropped anyway so every cluster follows one rule.

**6. Folder check before submitting.** `_require_odo_out_dir` becomes
`_require_group_writable(globus, collection, base)` for Odo and Frontier. It
lists `dirname(base)` and finds the entry named `basename(base)`:
- entry missing: refuse, giving `mkdir -p -m 2775 <base>`;
- entry has no group-write bit: refuse, giving `chmod 2775 <base>`;
- listing fails with `GlobusSessionExpired`: re-raise;
- listing fails with anything else (for example the parent is not listable
  under the researcher's identity): log it and continue.

*Alternative:* list `<base>` itself. Rejected: a Globus listing reports its
entries' permissions, not those of the folder it lists.

**7. No registry.** `SubmittedJob`, `_submitted_jobs`, the persistence helpers
and `_submitted_account` are deleted, and so are `list_hpc_jobs` and its
entries in `HPC_TOOLS` (`agents.py`), the molten-salt system prompt,
`ui/lib/tool-labels.ts` and `docs/project-onboarding.md`.
`_resolve_cluster(cluster, cfg)` loses its `job_id` lookup. Status, outputs and
cancel check `dry_run.is_dry_job(job_id)` before resolving a cluster, so
dry-run jobs keep working without credentials. `dry_run` keeps its own
in-process `_dry_jobs`. The Perlmutter paths no longer say "only for jobs
submitted in the current session".

**8. Lux passes no account.** `slurm_ssh.render_batch_script` loses its
`account` parameter and writes no `-A` directive. Lux was its only caller, so
an optional parameter would only ever have been `None`. `lux_account` and
`lux_remote_dir` are deleted from both configs. The Lux credential check stops
reporting a project.

**9. The example job writes under `$VISTA_OUT`.** Both scripts create the venv
at `"$VISTA_OUT/.venv"`. On Odo the source folder is read-only to the
automation user, so creating it in the working directory fails there today.

## Risks / Trade-offs

- [A hosted install relied on the shared Globus login] → its researchers see
  "Globus not connected" and connect their own. This is listed as breaking in
  the proposal and the README.
- [Jobs submitted before the upgrade live under `<session_id>/out`] → status
  still reports their IRI state, and their logs show as absent. The old
  `hpc_job_registry.json` is left on disk untouched and is never read.
- [The folder check cannot see the permissions, and the folder is not
  group-writable] → the job fails at the facility with no log. The settings
  field and the README state the requirement.
- [Existing projects' tool lists name `!list_hpc_jobs`] → an exclusion of a
  tool that does not exist is a no-op. Verify this in `test_seed_projects.py`.
- [Researchers sharing a folder can read each other's outputs] → intended: the
  facility's group permissions decide, and sharing a folder is a choice.

## Migration Plan

1. Deploy. New nullable columns are added on startup by `_add_missing_columns`.
2. Each researcher sets the remote directory for each OLCF or Lux cluster they
   use. The folder must be group-writable on Odo and Frontier. Researchers on
   the old defaults can enter the old paths
   (`/gpfs/wolf2/olcf/gen150/proj-shared/vista`,
   `/lustre/orion/chm243/proj-shared/vista`), which already meet this.
3. Unset the `VISTA_MCP_*_GLOBUS_*` variables; they are ignored.

Rollback is the previous release. Nothing this change writes is read by the old
code, apart from the new columns, which the old code ignores.
