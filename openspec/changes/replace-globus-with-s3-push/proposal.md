## Why

OLCF file ops go through Globus Transfer under a deployment-wide refresh token. Both
OLCF collections are High Assurance with `authentication_timeout_mins: 4320` — a
3-day authentication assurance timeout, verified against the live Transfer API.
Refreshing a token does not reset that clock (derived tokens inherit the parent
token's session) and Globus Auth sessions are per-OAuth-client, so only an
interactive browser re-login against the cluster's SSO domain restores access.

That is a twice-weekly per-cluster chore, but worse, it breaks the normal case:
Frontier queue waits routinely exceed 72 hours, so a job can be submitted, sit past
the expiry, run, succeed — and then Vista cannot fetch its logs or outputs, reporting
"no output files yet", which reads to a user as a failed job.

## What Changes

- Invert the transfer direction: the job **pushes** `$VISTA_OUT` and its Slurm logs to
  an S3 bucket Vista owns; Vista reads them back with its own instance credentials
- Remove `lib/globus.py`, the Globus Connect Personal endpoint (`scripts/launch_globus.py`
  and its `scripts/launch.sh` service), the four Globus settings, and `FakeGlobusClient`
- Inline job source files into the IRI JobSpec instead of transferring them, deleting
  `_sync_job_sources` and `_require_odo_out_dir` with its group-writable permission dance
- Add a `$VISTA_SCRATCH` job contract: `$VISTA_OUT` is uploaded verbatim, scratch is not
- Keep Perlmutter on the IRI filesystem API, and keep the Globus-Auth-issued NERSC IRI
  token flow in `scripts/get_globus_token.py`

## Capabilities

### New Capabilities

- `hpc-output-transfer`: Compute-node S3 push for clusters whose IRI service has no storage scope, plus the `$VISTA_OUT` / `$VISTA_SCRATCH` job contract

### Modified Capabilities

- `hpc-job-contracts`: the fake-client requirement replaces `FakeGlobusClient` with `FakeS3Client`
- `testing-ci`: the hermetic-CI and fake-at-boundaries requirements name S3 instead of Globus

## Impact

- `mcp_servers/vista_mcp_server/src/vista_mcp_server/`: new `lib/s3.py` and
  `jobscripts/s3_put.py`; `submit_job_mcp.py` OLCF paths; `config.py` settings; `boto3` dep
- Deleted: `lib/globus.py`, `scripts/launch_globus.py`, `tests/fakes/globus.py`, `globus-sdk`
- `hpc_jobs/**`: three job scripts repointed at `$VISTA_SCRATCH`; staged-via-Globus prose
- Deployment: new `VISTA_MCP_S3__*` env vars and an S3 bucket with one read/write (never
  delete) IAM key shared by the job push and Vista's reads; `GLOBUS_SETUP_KEY` and both
  `*_GLOBUS_REFRESH_TOKEN` vars retired
- Docs: `README.md`, `AGENTS.md`, `.env.sample`, `aws/.env.sample`, `docs/skill-onboarding.md`

## Non-goals

- No VISTAGuard work. The `vista-globus-untrusted-endpoint` semgrep rule scans
  agent-generated code, not Vista's own, and stays as-is.
- No change to the Perlmutter / NERSC IRI file-transfer path.
- No SSO / auth work, and no per-user isolation of job outputs. Cross-user visibility of
  jobs is already the status quo (one shared job registry) and is accepted for the beta.
- No per-job or short-lived S3 credentials: every time-bound AWS mechanism (STS ≤ 36 h,
  presigned URLs ≤ 7 days) expires sooner than a queue wait, reintroducing this defect.
