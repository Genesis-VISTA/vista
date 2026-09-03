## Context

Compute on all three clusters goes through an IRI service. File transfer diverges: the
NERSC IRI token authorizes both compute and storage, so Perlmutter uses
`IriClient.mkdir/ls/head/upload/download`. The OLCF AmSC tokens carry no storage scope —
`create_odo_iri_client` pins the compute resource and never resolves a storage resource at
all — so Odo and Frontier have used Globus Transfer under a deployment-wide refresh token.

That refresh token is now known to be unusable for unattended operation: both OLCF
collections are High Assurance with a 3-day `authentication_timeout_mins`, which a token
refresh cannot reset, and which is shorter than a Frontier queue wait.

## Goals / Non-Goals

**Goals:**

- Unattended, non-expiring output retrieval from Odo and Frontier
- Preserve the existing tool contracts: `get_hpc_job_status` shows state + logs + a file
  list; `get_hpc_job_outputs` returns `/mnt/data/output/<job_id>/<relpath>` sandbox paths
- Restore binary output support (IRI `download` is text-only; forge-tune writes `.pt`)
- Delete the Globus-shaped incidental complexity (GCP endpoint, one-level mkdir, no
  recursive ls, group-writable permission dance)

**Non-Goals:**

- Changing the Perlmutter / IRI file path
- VISTAGuard changes
- Per-user isolation of job outputs
- Live log tailing while a job runs

## Decisions

1. **The job pushes; Vista never reaches into the cluster.**
   - Rationale: removes the only credential subject to an OLCF-side session policy. A
     compute node needs outbound HTTPS through the already-exported OLCF proxy and
     nothing else.

2. **One long-lived S3 key shared by all jobs and by Vista's own reads, delivered to
   the job via the JobSpec `attributes.environment`.**
   - Rationale: every scoped, short-lived alternative expires inside the queue —
     `sts:AssumeRole` 12 h (1 h chained), `sts:GetSessionToken` 36 h, presigned PUT 7 days
     and one URL per object for an output list that isn't known until the job ends. A
     static key is the only mechanism that survives a two-week queue wait.
   - Blast radius is bounded by policy rather than lifetime: `s3:PutObject` +
     `s3:AbortMultipartUpload` + `s3:GetObject` on `<bucket>/jobs/*` plus `s3:ListBucket`
     on the bucket, and deliberately **no `s3:DeleteObject`**, plus bucket versioning. A
     key lifted off the cluster can read and overwrite job output but cannot destroy it.
   - Sharing one key between the push and Vista's reads is a deliberate simplification
     for the beta: it removes the need to wire an instance-profile role and halves the
     deployment config. The cost is specific and worth naming — the credential is visible
     to anyone who can inspect a job on the cluster (job env, batch script,
     `scontrol show job`, IRI service records), so it turns "reading job output requires
     OLCF project access" into "reading job output requires the string", off-facility and
     for every cluster's output at once. Accepted because the data is open-enclave
     simulation output from a shared project account that those same people can already
     read on `proj-shared`, and because cross-user isolation is already out of scope.
     Withholding `DeleteObject` is what keeps the downside an annoyance rather than the
     loss of weeks of compute.
   - Splitting them again (write-only on the cluster, reads via an instance role) needs a
     second settings pair; it is not expressible with one, because `lib/s3.py` uses the
     configured key for reads whenever it is set.
   - Named `VISTA_S3_KEY_ID` / `VISTA_S3_SECRET`, deliberately **not** `AWS_*`:
     VISTAGuard's G5 gate treats `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` as
     credential-bearing env vars job scripts must not touch, so `AWS_*` names would make
     every Vista job trip Vista's own security gate.

3. **A stdlib-only SigV4 uploader shipped as `jobscripts/s3_put.py`, base64-inlined into
   the JobSpec.**
   - Rationale: `aws`, `curl`, `wget` and `tar` appear nowhere in `hpc_jobs/` (the single
     `curl` use is Perlmutter-only), so no OLCF compute-node availability can be assumed.
     `python3` after `module load cray-python` is the one interpreter the catalog already
     relies on everywhere. Keeping it a real repo file makes it lintable and unit-testable
     rather than an unverifiable string.
   - It must implement multipart above S3's 5 GiB single-PUT ceiling, because forge-tune
     writes checkpoints into `$VISTA_OUT` and there is no size cap.

4. **Per-file objects plus a `manifest.json` written last.**
   - Rationale: preserves selective retrieval (`display_file` on one PNG must not pull a
     multi-GB tarball) and lets `ListObjectsV2` replace the recursive Globus BFS-ls in one
     paginated call. The manifest doubles as a completion sentinel, making "job finished
     but the push did not" a distinguishable, reportable state.

5. **`trap … EXIT` installed by the existing setup snippet, not an appended suffix.**
   - Rationale: job bodies `exit` and run under `set -e`, and a failed job is exactly when
     the log matters. The trap also fires on Slurm's SIGTERM at the time limit.

6. **`$VISTA_OUT` is uploaded verbatim; `$VISTA_SCRATCH` is the working directory.**
   - Rationale: an exclude list inside the uploader is guesswork about job internals. A
     two-directory contract is something a job author can follow without knowing the
     transport, and gives the trap a single thing to clean up — which matters because
     nothing on the cluster can delete files any more.
   - Consequence: the current `export HOME="${HOME:-$VISTA_OUT}"` becomes `$VISTA_SCRATCH`,
     and `RUN_DIR_<Cluster>` moves under scratch so inlined sources and job-built venvs
     share that cleanup. Exported on all three clusters so job scripts stay portable.

7. **Sources are inlined into the JobSpec rather than transferred.**
   - Rationale: the whole catalog stages 47 KB across 6 files, so base64 heredocs in the
     prefix cost nothing and delete both `_sync_job_sources` and the need for any
     cluster-side read credential.

8. **Odo adopts Frontier's session-scoped `{base}/{session_id}/out` layout.**
   - Rationale: Odo's flat `{base}/out` and `mkdir -p -m 2775` existed only because Globus
     created those dirs with the DTN's umask and locked out the IRI auser. Slurm creates
     missing parents as the auser itself, so the three clusters can share one shape.

## Risks / Trade-offs

- [S3 unreachable through the OLCF proxy] → The repo already pip-installs and git-clones
  through `proxy.ccs.ornl.gov:3128` from compute nodes, so HTTPS CONNECT works, but
  `*.s3.amazonaws.com` is unverified. Validated first by an `example` job on Odo; the
  `lib/s3.py` boundary keeps a swap to an ORNL-side store local.
- [No `python3` on PATH inside the trap] → Job bodies `module purge` and load their own
  toolchains; the trap resolves an interpreter defensively rather than assuming one.
- [Upload time is now on the job's clock] → A killed job may not finish its push. The
  manifest sentinel makes truncation detectable; large-output jobs may later need reserved
  time or a background sync.
- [Logs only appear at job end] → Accepted regression versus the previous partial-log
  fetch. `_PRE_RUN_STATES` and the IRI state still report progress.
- [Objects accumulate] → S3 lifecycle expiry plus versioning, set at bucket creation.

## Migration Plan

1. Create the bucket, the IAM user and key (PutObject / AbortMultipartUpload /
   GetObject on `<bucket>/jobs/*` + ListBucket on the bucket, no DeleteObject),
   versioning, and a lifecycle rule.
2. Set `VISTA_MCP_S3__*` in `.env` / `aws/.env`; leave the Globus vars in place until cut.
3. Land the code change; both `*_GLOBUS_REFRESH_TOKEN` vars and `GLOBUS_SETUP_KEY` become
   inert and can be deleted from deployment env.
4. Validate `hpc_jobs/example` on Odo, then Frontier, then a scratch-heavy job
   (`salt-chemistry-md`) to confirm its git clone does not upload.
5. Stop running `scripts/launch_globus.py`; `data/globusonline/` and `data/gcphome/` can be
   deleted from the data volume.

## Open Questions

- Does `forge-tune`'s multi-GB checkpoint push fit inside its Slurm allocation, or does it
  need reserved wall-clock at the end of the job?
- Where should admin-pre-staged large inputs live now that sources are inlined?
  `Molten_Salt_Thermophysical_Properties.csv` is gitignored and absent from the repo, yet
  `forge-tune`'s setup script requires it — a `{base}/{job}/aux/` convention is proposed as
  a follow-up.
