# Proposal

## Why

S3M now serves Lux, so the SSH workaround VISTA uses there (`sbatch` over a PIN + RSA
login) can go and every cluster can be reached one way: one IRI client of VISTA's own, at
each cluster's API version. Lux is reachable only on S3M's IRI v2 (v1 lists it but answers
404 to a submission), so the OLCF clusters move to v2. Perlmutter stays on NERSC's v1,
since NERSC's v2 refuses the NERSC IRI tokens researchers hold. Lux's arrival also broke
Frontier on v1: discovery lists Lux first and the Frontier client's fallback sends Frontier
jobs there. Naming each resource by configured id fixes that, and dropping amscrot (v1
only, then unused) removes VISTA's only private dependency. Live evidence for Lux (v2) and
Perlmutter (v1, without amscrot) is in design.md, Context.

## What Changes

- All four clusters go through one IRI client, each named by its configured compute
  resource id (never discovery or fallback) at its configured API version: v2 for Odo,
  Frontier and Lux, v1 for Perlmutter, whose file operations use the same client's tasks
  (binary downloads now work).
- **BREAKING (build):** `amscrot-py`, the MCP `hpc` extra and the `AMSC_GIT_TOKEN` / AWS PAT
  build secrets are removed.
- **BREAKING:** Lux uses S3M + Globus instead of SSH: its own S3M token (`lux_s3m_token`,
  charging the token's project; the Lux account is unused), Frontier's Globus connection,
  and the Odo/Frontier layout and permission rules. The SSH path, login prompt and SSH
  probe are removed.
- **BREAKING (skills):** `setup_lux.sh` runs in the allocation before the job script, not
  on the login node. A Lux job's `partition` becomes the IRI queue; `gpus_per_node`
  becomes GPUs per process.
- The transfer tools accept `lux`. Readiness checks each cluster at its API version.
- Bundled skills (`hello-lux`, `hpc-skill-author`) and HPC/build docs updated.

**Non-goals:** Perlmutter on IRI v2 (needs a token NERSC's v2 accepts; a later change
flips one setting); IRI endpoints VISTA does not use today; transfer tools for Perlmutter;
an SSH fallback; a job registry; migrating Lux jobs submitted over SSH; VISTAGuard. Nothing
is taken from the unmerged `lux-iri-support` branch.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `hpc-job-submission`: one IRI client, versions and resource ids per cluster; Perlmutter filesystem tasks; Lux on S3M/IRI; Lux account removed.
- `hpc-availability`: facility checks by resource id at each cluster's version; Lux token, credential and shared Globus checks; SSH probe removed.
- `hpc-file-transfer`: transfer tools accept Lux.
- `hello-cluster-skills`: `hello-lux` needs the Lux token and Globus, not an account.
- `hpc-skill-authoring`: Lux setup runs in the allocation.
- `hpc-job-contracts`: hermetic IRI client coverage (compute for all clusters, NERSC filesystem tasks); no private package in tests.
- `chat-runs`: Lux no longer asks for an SSH login.
- `laptop-distribution`: job submission needs no private repository.

## Impact

- MCP: `lib/iri.py` rewritten (httpx), `submit_job_mcp.py`, `transfer_mcp.py`, `config.py`,
  `lib/user_config.py`, `lib/globus.py`; `lib/slurm_ssh.py` deleted; `amscrot-py` dropped.
- Backend: `lux_s3m_token` (additive), settings models, MCP metadata, `services/hpc_status.py`.
- UI: Lux settings section and card.
- Build/docs: `.gitlab-ci.yml`, `.github/scripts/package.sh`, `aws/`, README, `docs/*`
  (development, building-packages, releasing, hpc, hpc-jobs), AGENTS.md; skills `hello-lux`,
  `hpc-skill-author`.
