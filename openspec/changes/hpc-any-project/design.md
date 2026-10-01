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

Two facility facts shape the layout, both checked against OLCF:
- S3M tokens are not authorized for IRI filesystem operations, so VISTA cannot
  create or `chmod` anything as the project's IRI automation user. Globus can
  create folders, but only as the researcher, with the server's umask (755),
  and has no way to change a mode afterwards.
- OLCF's Slurm creates missing folders in a job's `--output` path, as
  whichever user runs the job. So the automation user can create the log folder
  itself, wherever it may write.

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

**4. One path helper, two sibling folders.**
```
OLCF:       RemoteLayout(base, user): jobs=<base>.<user>.jobs  out=<base>.out
Perlmutter: RemoteLayout(base, nested=True): jobs=<base>/jobs   out=<base>/out
src = <jobs>/<job>/src
stdout = <out>/log-%j.out   stderr = <out>/log-%j.err   VISTA_OUT = <out>/$SLURM_JOB_ID
job_dir = <out>/<job>   (VISTA_JOB_DIR; FORGE_MODEL_<cluster> defaults to <job_dir>/model)
```
`VISTA_JOB_DIR` and the `FORGE_MODEL_*` defaults used to sit beside the sources.
They hold what a job's runs share and write -- `forge-pretrain`'s checkout, a
downloaded model -- so they move under `.out`, the only folder the job's user
can write on Odo and Frontier. No job script names the old paths (only
comments, updated); a job's JSON can still override either.
Submit uses the `%j` templates. Status and outputs substitute the job id. The
IRI `directory` / Lux `workdir` is `<base>.jobs`; Odo's setup snippet still
does `cd <src>`. `<base>` itself is never created.

Why two folders: on Odo and Frontier, what Globus creates belongs to the
researcher (755), and what Slurm creates belongs to the automation user. Each
can write only in its own folder, so the sources and the outputs cannot share
one folder unless the researcher makes it group-writable by hand. Side by side,
each folder is created by the only identity that writes to it, and the only
requirement is the one OLCF already meets: `proj-shared` is group-writable
(770), so the automation user can create `<base>.out` beside `<base>.jobs`.
*This is a temporary workaround until S3M supports IRI filesystem operations*,
which would let VISTA create one folder as the automation user and `chmod` it;
the layout then can go back to a single folder.
*Alternative:* one folder that the researcher creates with `mkdir -m 2775`.
Rejected after a fresh setup hit it: it is a manual step on every new folder. `session_id` is deleted from `AppSettings`. The metrics
`session_id` is a different field and is unaffected. `IriDefaults.account` and
`IriDefaults.remote_dir` are deleted. `IriDefaults` ignores unknown keys, so an
old `cluster_defaults.json` still loads.

**5. Nothing the job writes is made through Globus.** Frontier stops calling
`operation_mkdir_p` for the out folder. On OLCF (Odo, Frontier, Lux) Slurm
creates `<base>.out`, and the job prefix runs `mkdir -p -m 2775 "$VISTA_OUT"`
on every cluster, so outputs in a shared folder stay group-writable. Sources are
uploaded through Globus under `<base>.jobs/<job>/src`, which the automation
user only reads; the Globus `mkdir` walks down from the parent of `<base>`.
Perlmutter and Lux run as the researcher, and only Odo's and Frontier's Slurm
have been seen to create a missing log folder (upstream Slurm does not), so
VISTA creates `<base>.out` on those two before submitting -- through IRI on
Perlmutter, over SSH on Lux -- which is harmless if Slurm would have.

**6. Folder check before submitting.** `_require_odo_out_dir` becomes
`_require_writable_out(globus, collection, layout)` for Odo and Frontier, with
at most two Transfer `stat`s (`GlobusClient.operation_stat`), each reading one
entry:
- `stat <base>.out`: if it exists, continue. A `.out` that Slurm made is 755
  but owned by the automation user, so its mode says nothing about whether that
  user can write in it, and every job keeps it group-writable anyway; a file
  there is refused;
- otherwise `stat` the parent of `<base>`: not found, or no group-write bit,
  refuse;
- every refusal names one command, `mkdir -p -m 2775 <base>.out`, which works
  whatever the parent's mode, because the researcher creates the folder with
  group write;
- a `stat` that fails with `GlobusSessionExpired`: re-raise; with anything else
  (for example an entry the researcher cannot read): log it and continue.

To tell "not found" from "could not look", a Transfer `stat` or plain
(non-recursive) `operation_ls` of a missing path raises `GlobusFileNotFound`,
the type the HTTPS side already raises, instead of the raw `TransferAPIError`.

*Earlier version, replaced in review:* listing the parent and the grandparent to
read the two entries. `proj-shared` and the project's folder can be large, and
`stat` reads exactly the entry needed.

**7. No registry.** `SubmittedJob`, `_submitted_jobs`, the persistence helpers
and `_submitted_account` are deleted, and so are `list_hpc_jobs` and its
entries in `HPC_TOOLS` (`agents.py`), the molten-salt system prompt,
`ui/lib/tool-labels.ts` and `docs/project-onboarding.md`.
`_resolve_cluster(cluster, cfg)` loses its `job_id` lookup and is used only by
submission. Status, outputs and cancel take `cluster` as a required argument:
with no registry, inferring it from "the only cluster with a token" would send
a Lux job's cancel (Lux needs no token) to whichever job shares its id on that
cluster. `submit_hpc_job`'s result names the cluster, and its description, the
skills and the system prompts tell the agent to pass it back. Status and cancel
check `dry_run.is_dry_job(job_id)` before anything else, so dry-run jobs need
no credentials. (Outputs never served dry-run jobs.) `dry_run` keeps its
own in-process `_dry_jobs`. Cancellation needs no remote folder. The Perlmutter paths no longer say "only for jobs
submitted in the current session".

**8. The Lux account is a user setting.** OLCF's Slurm needs an explicit
project, and Lux has no token to take one from, so `UserTable` gains
`lux_account`, `UserConfig.require_lux_account` refuses a missing or non-plain
value, and `render_batch_script` writes `-A <account>`. The deployment's
`lux_account` and `lux_remote_dir` are deleted from both configs. The Lux
credential check reports the researcher's account as its project.
*Earlier choice, reversed:* passing no `-A` and relying on a default account,
which review found OLCF's Slurm normally rejects.

**10. Per-researcher sources, shared outputs (review findings 5 and 8).** On
OLCF the sources folder carries the researcher's account, `<base>.<user>.jobs`,
so two researchers sharing `<base>` never write into each other's 755 folder:
Globus creates folders only as the researcher and cannot chmod. The account is
the owner Globus reports for `/~/` (`GlobusClient.home_owner`: one Transfer
`stat`, cached per collection and credential; it differs between enclaves, so it
is asked per cluster) and the SSH login on Lux (`slurm_ssh.username`). Status
and outputs never need it: they read `.out`. The output folder stays shared, and
every OLCF job begins with `_shared_out_prefix`: `umask 002`, then a best-effort
`chgrp <project>` and `chmod 2775 <base>.out`, which succeed only for its owner
-- whichever identity made it -- so the first job fixes it for all later ones,
and setgid keeps the group on everything below. On Lux VISTA runs the same lines
when it creates `.out`, and the login-node setup script runs under `umask 002`.
That lets the automation user, the researcher's Lux jobs and colleagues all
write `.out` and `VISTA_JOB_DIR`. Perlmutter jobs do not run it: their folder is
the researcher's own and nothing else writes it, so NERSC's default
permissions are left alone. `VISTA_REMOTE_BASE` is no longer exported, since
`<base>` itself is never created on OLCF.

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
- [The folder check cannot see the permissions, and the parent is not
  group-writable] → the job fails at the facility with no log. The settings
  field and the README state the requirement.
- [A researcher creates `<base>.out` by hand without `-m 2775`] → the check
  accepts any existing `.out`, so the job fails with no log. The refusal and
  the README give the command with the mode.
- [`VISTA_JOB_DIR` moves from `<base>/<job>` to `<base>.out/<job>`] → a job's
  shared state (for example `forge-pretrain`'s checkout) is fetched again into
  the new place on its first run.
- [Frontier and Lux sharing one remote folder] → their Slurm job ids can
  collide, mixing `log-<id>.out` and `<id>/` of two jobs. Rare: Frontier's ids
  are far higher. Accepted.
- [A project's Unix group is not named like its project id] → the `chgrp` fails
  quietly, and `.out` keeps the group it was created with, typically the
  project's through `proj-shared`'s setgid.
- [Two sibling folders instead of one] → `<base>` names a pair the researcher
  never sees as one folder. The settings hint and README say so. Temporary,
  until S3M supports IRI filesystem operations.
- [Existing projects' tool lists name `!list_hpc_jobs`] → an exclusion of a
  tool that does not exist is a no-op. Verify this in `test_seed_projects.py`.
- [Researchers sharing a folder can read each other's outputs] → intended: the
  facility's group permissions decide, and sharing a folder is a choice.

## Migration Plan

1. Deploy. New nullable columns are added on startup by `_add_missing_columns`.
2. Each researcher sets the remote directory for each OLCF or Lux cluster they
   use. On Odo and Frontier the folder containing it must be group-writable,
   which any folder directly under `proj-shared` already is. A researcher who
   enters an old path such as `/gpfs/wolf2/olcf/gen150/proj-shared/vista` gets
   `vista.jobs` and `vista.out` beside it, and VISTA no longer finds jobs under
   the old `vista/out/`.
3. Unset the `VISTA_MCP_*_GLOBUS_*` variables; they are ignored.

Rollback is the previous release. Nothing this change writes is read by the old
code, apart from the new columns, which the old code ignores.
