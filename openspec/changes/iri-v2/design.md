# Design

## Context

See proposal.md for motivation. Current state:

- `lib/iri.py` wraps amscrot (`amsc_iri`), whose generated client hard-codes
  `/api/v1/...` in every path and hides NERSC's filesystem task polling. Odo pins its
  compute UUID (`odo_compute_resource_id`); Frontier and Perlmutter find theirs by
  `discover()`, Frontier falling back to `compute[0]` (now Lux). Perlmutter picks the first
  storage resource with "home" in its name.
- `submit_job_mcp.py` has near-copies of the submit path per cluster
  (`_submit_odo_job`, `_submit_frontier_job`, `_submit_perlmutter_job`, `_submit_lux_job`);
  Lux's is SSH (`lib/slurm_ssh.py`, the per-chat PIN + RSA elicitation in `lib/ssh.py`).
- The backend reads the MCP server's `VISTA_MCP_*` HPC settings (`hpc_settings`, checked by
  `backend/tests/test_settings_defaults.py`). `services/hpc_status.py` calls
  `/api/v1/status/resources` itself with httpx; Lux uses an SSH greeting probe.

Probed facts (2026-11-06):

- S3M, both enclaves: `S3M IRI API 2.0.0`; compute `POST /api/v2/compute/job/{r}`,
  `GET /api/v2/compute/status/{r}/{job}`, `DELETE /api/v2/compute/cancel/{r}/{job}`;
  ids `odo` (open), `frontier`, `lux` (moderate). Live on Lux: `Job` is
  `{id, status: {state, time, message, exit_code, meta_data}}`, states lower case, a
  queued job reports `exit_code: 0`, `message: "None"`; cancel answers 204.
- NERSC (`api.iri.nersc.gov`) serves v1 and v2 with the same compute paths and job fields
  as S3M. **v2 refuses researchers' NERSC IRI tokens** (401: "AmSC authentication failed:
  Not enough segments || Facility Specific authentication failed: 403"); it wants an AmSC
  PAT or a Globus token for the IRI scope. v1 accepts them, so Perlmutter stays on v1.
  Compute resource `94351904-6dba-4c16-b5cd-fbd280d8615b` (`compute`; `login` and `jobs`
  are listed too); storage `homes` `65b28619-c3b6-4942-8da1-044a3b3a2a9e`.
- NERSC v1, live through plain httpx (job `60945807`, project `m4724`): filesystem
  operations are tasks: `POST mkdir` (JSON `{path, parent}`), `POST upload?path=`
  (multipart `file`), `GET ls|head|stat|download?path=...`, `DELETE rm?path=`, each
  answering `{task_id, task_uri}`; `GET /api/v1/task/{id}` gives `{id, status, result}`,
  status `pending | active | completed | failed | canceled`, each finishing in 1.5–6 s.
  Results: `ls` → `{"output": [{name, type: "d"|"f", user, group, permissions,
  last_modified, size}]}` with `name` the **resolved absolute path** (`/global/u1/...` for a
  `/global/homes/...` request) and the listed folder itself included; `head` →
  `{"output": {"content", ...}}`; `stat` → `{"output": {size, mtime, ...}}`; `download` →
  `{"output": <str>}`, **base64 for a binary file but the raw text for a text file**; a
  missing path → task `failed`, `{"error": "...Error: 404: ... No such file or directory"}`.
  Compute: submit → `{id, status: {state: "queued"}}`; status `exit_code` 0 while queued,
  `message` null; `queue_name` is the QOS (job ran in `debug` QOS, partition
  `regular_milan_ss11`); `HOME` is set; `%j` expands; `Idempotency-Key` ignored (no reply
  header); cancel → 204 then `canceled`.

## Goals / Non-Goals

**Goals:** one IRI client for every cluster, the API version a per-cluster setting; resources addressed only by configured id;
per-cluster differences reduced to data (URL, ids, token, file transport, script names);
hermetic tests for every request VISTA makes; no private dependency.

**Non-Goals:** Perlmutter on v2 (its v2 refuses today's NERSC tokens); endpoints VISTA
does not use today; retries of a submit POST (neither facility honours `Idempotency-Key`
on the paths VISTA uses); removing `lib/ssh.py` (agenthpc uses it).

## Decisions

### 1. VISTA's own IRI client, replacing amscrot

`lib/iri.py` is rewritten as an async httpx client:

- `IriTarget` (frozen): `cluster`, `base_url`, `api_version` (`v1` | `v2`),
  `compute_resource_id`, `storage_resource_id | None`, built from settings by
  `iri_target(cluster)`. Compute calls differ between versions only in the path prefix.
  Filesystem calls differ in method and argument placement (v1: `GET`/`DELETE` with query
  parameters, v2: `POST` with a JSON body), kept in one table keyed by operation.
- `IriClient(target, token)`: `submit_job(spec, name) -> str`,
  `get_job_status(job_id) -> dict`, `cancel_job(job_id)`; and, when the target has a
  storage resource, `mkdir`, `ls`, `head`, `download`, `upload`, each submitting a task and
  awaiting it with `_await_task` (poll with backoff from 0.5 s to 5 s, bounded per
  operation; default 120 s, uploads and downloads scaled by size).

Why not amscrot: it cannot reach v2 without patching generated paths, it is the reason
builds need a private GitLab token, and VISTA already builds the full v2 payload itself.
Alternatives: patching `amsc_iri` paths at runtime (fragile, hidden from its maintainers);
waiting for an upstream v2 amscrot (blocks Frontier's fix and Lux on another team). The
module's interface is small enough that an upstream SDK could replace it later without
touching callers.

Normalization: state upper-cased; `exit_code` kept only for terminal states (COMPLETED,
FAILED, CANCELED, TIMEOUT); `message` dropped when empty or `"None"`; `meta_data` dropped.
Errors become `ToolError`s: 401/403 → token rejected, mint a new one (naming the cluster's
token setting); 404 on a compute call → unknown resource, naming cluster and id; a
filesystem task's 404/`ENOENT` result → `IriFileNotFound` (callers render "(no logs yet)");
task `failed` → `IriTaskFailed` with the task's reason and path; bound reached →
`IriTaskTimeout`; other status or timeout → the status and the first 300 characters of the
problem body. A POST submit that times out is reported as "may have been submitted; check
status before resubmitting", never retried.

Task results follow the recorded v1 shapes (Context). Two need care. `ls` names entries
by resolved path, so the client resolves the requested root from the listing's first entry
(the folder itself) and rewrites names relative to the path the caller asked for. A
`download` output is base64 for some files and raw text for others; the client `stat`s the
file first and accepts the raw text when its UTF-8 length equals the size, else base64 when
the decoded length equals the size, and otherwise fails naming the file (never saving it
altered). Binary outputs, which amscrot could not download, become possible.

### 2. Configuration by cluster: base URL, API version, resource ids

MCP `config.py`: `<cluster>_iri_url` for all four (adding `lux_iri_url`, default
moderate), `<cluster>_iri_api_version` (`v2` for OLCF, `v1` for Perlmutter),
`<cluster>_iri_resource_id` (defaults `odo`, `frontier`, `lux`,
`94351904-6dba-4c16-b5cd-fbd280d8615b`), and `perlmutter_iri_storage_id` (default `homes`,
`65b28619-…`). Moving Perlmutter to v2 later is `perlmutter_iri_api_version=v2` plus a token
v2 accepts. Removed: `odo_compute_resource_id`, `frontier_machine`, `nersc_machine`,
`odo_machine`, `lux_ssh_hosts`, `lux_proxy`. `nersc_iri_url` is kept as an alias read
into `perlmutter_iri_url` for one release so existing `.env` files keep working. The
backend's `hpc_settings` mirrors the same names; the parity test is extended.

### 3. One submit path with a per-cluster profile

`_submit_job(cluster)` replaces the four functions. A frozen `ClusterProfile` per cluster
holds: job and setup script names, the `RUN_DIR_<Cluster>` suffix, image/module env names,
the file transport (`globus` with a collection id for OLCF, `iri` for Perlmutter), the
layout (`nested` for Perlmutter), the account source (S3M introspection for OLCF, the NERSC
account setting for Perlmutter), whether to `cd` into the source folder (Odo only), and a
resource mapper. Lux's mapper sends `iri.partition` (or an explicit `queue_name`) as
`queue_name` and `gpus_per_node // processes_per_node` as `gpu_cores_per_process`;
Perlmutter keeps its `constraint` custom attribute, shifter image and
`SLURM_GPUS_PER_NODE`. Status, outputs and cancel likewise dispatch on the profile's file
transport rather than the cluster name.

The setup script is inlined into the job body after the shared prefix (for OLCF: umask,
`VISTA_OUT`, `HOME` fallback, proxy, `module purge`), run in a login bash of its own
(`bash -lc`) whose non-zero exit ends the job with the reason in its stderr. It does not
add `-e`: every bundled setup script sets `set -euo pipefail` itself, and `-e` from the
command line would also apply to the login profile (Lux's automation user's profile has a
failing line). Its own shell keeps its exports out of the job script, as `pre_launch` did. Today it is IRI `pre_launch`,
which runs before the prefix and so sees neither `VISTA_OUT` nor the proxy. Perlmutter's
prefix is unchanged apart from this ordering.

### 4. Credentials: own S3M token for Lux, Frontier's Globus

`UserConfig.lux_s3m_token`; `require_s3m_token("lux")`; introspection unchanged.
`require_globus_token("lux")` and the backend's Globus selection normalize `lux` →
`frontier` before choosing fields, so there are no `lux_globus_*` columns; error text
names the cluster the researcher used. `globus.py`'s `Cluster` gains `lux` with the
Frontier collection. `lux_account` stays in the database, unread. Perlmutter keeps
`nersc_iri_token` and `nersc_account`.

Backend: `local_settings.lux_s3m_token` as `EncryptedStr`, nullable (additive; the column
adder skips a column that already exists, as on installs that ran the unmerged
`lux-iri-support` branch). Added to the settings update/public models (write-only) and
the MCP metadata; `HPC_TOOLS` needs no change.

### 5. Readiness at each cluster's version

`hpc_status.py` calls `status/resources`, `status/incidents` and `compute/resources` at
each cluster's configured version (v2 for OLCF, v1 for Perlmutter), matching by configured
resource id. Lux joins the OLCF branch with its token and Frontier's Globus pair.
`LuxProbe`, `probe_ssh_greeting`, `LUX_PROBE_*` and `LUX_SIGN_IN` are deleted.

### 6. Removals

- Lux SSH: `lib/slurm_ssh.py`, `_lux_conn`, `_sync_job_sources_ssh`, `_get_lux_job_*`, and
  their tests. `lib/ssh.py` and the generic elicitation path stay for agenthpc.
- amscrot: the `hpc` extra and `override-dependencies` in `pyproject.toml`,
  `test_amscrot_log_location.py`, the lazy-import comments, the CI note in `.gitlab-ci.yml`,
  `AMSC_GIT_TOKEN` in `.github/scripts/package.sh` and the release workflow, the PAT
  secret in `aws/Dockerfile.server` / `aws/build-image.sh`, and the setup sections in
  README, `docs/development.md`, `docs/building-packages.md`, `docs/releasing.md`.

## Risks / Trade-offs

- [Every cluster changes at once] → task groups land one cluster family at a time behind
  hermetic tests; live `hello-*` runs on all four before merge (group 9). The v1 Frontier
  pin (group 1) ships first on its own.
- [Perlmutter left on v1] → by configuration only; when researchers can obtain a token
  NERSC v2 accepts, a follow-up flips the version and documents the token in Settings.
  If NERSC retires v1 first, Perlmutter breaks as it would with amscrot today.
- [Download encoding decided by the server] → reconciled against `stat` size (§1); a
  mismatch fails naming the file. Covered by fixtures for a text and a binary file.
- [Losing amscrot features] → VISTA used only job submit/status/cancel and five filesystem
  calls; nothing else is lost.
- [Setup moved off the Lux login node] → a setup relying on login-node-only tools fails in
  the job, with its log; documented. External skill repos (`lux-skills`) told separately.
- [Jobs submitted to Lux over SSH before the upgrade] → their status may not be visible
  through IRI; documented, not migrated.
- [Release secrets removed while an older branch still needs them] → `AMSC_GIT_TOKEN` is
  removed from workflow use, not revoked; revoking is a manual step after the release.

## Migration Plan

1. Ship group 1 (v1 Frontier pin) immediately; it is independent.
2. Merge the rest as one MR after the live gate. Researchers add a Lux S3M token in
   Settings; Lux shows Not connected until they do. No data migration. Rollback is a
   revert (the new column is nullable and ignored by older code; restoring amscrot needs
   the build secret again).
3. After the first release without amscrot, revoke `AMSC_GIT_TOKEN`.
