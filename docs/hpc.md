# HPC clusters

How VISTA submits jobs to remote clusters, what each researcher connects, and where a job's
files go. To add or change the jobs the agent can submit, see [hpc-jobs.md](hpc-jobs.md).

The agent submits only predefined jobs from `hpc_jobs/`, to four clusters:

| Cluster | Compute | Files | Runs as |
|---|---|---|---|
| Odo (OLCF, open enclave) | AmSC IRI API, authorized by an S3M token | Globus | the project's IRI automation user |
| Frontier (OLCF, moderate enclave) | AmSC IRI API, authorized by an S3M token | Globus | the project's IRI automation user |
| Perlmutter (NERSC) | NERSC IRI API (`amscrot-py`) | NERSC IRI filesystem API | the researcher |
| Lux (OLCF) | `sbatch` over SSH, since Lux has no IRI service | SFTP on the same connection | the researcher |

## Credentials

Per-user HPC credentials are **not** env vars: each researcher connects them in the UI, in each
cluster's section of Settings, and nothing is exported before VISTA starts.

- **S3M (Odo, Frontier).** An S3M token is scoped to one OLCF project, and that project is the
  Slurm account the cluster's jobs are charged to. A token from any project with S3M access
  works, and Odo and Frontier each need their own; mint them per the
  [S3M docs](https://docs.olcf.ornl.gov/services_and_applications/s3m/overview.html#get-a-token).
  They expire in 24 hours.
- **NERSC IRI (Perlmutter).** A NERSC IRI token.
- **Globus (Odo, Frontier).** A one-time authorization per cluster. Every Odo and Frontier file
  operation acts as that researcher's own identity, so the facility decides what they may read
  and write; there is no deployment-wide Globus login to fall back on. A researcher's own Odo or
  Frontier connection wins, falling back to one they connected for both.
- **Lux.** No token: the researcher logs in through the OLCF hub with their own passcodes, once
  per chat session. Each researcher also sets a **Lux account**, the OLCF project Lux jobs are
  charged to (`#SBATCH -A`), since there is no token to take a project from.

Absent Globus is never fatal; it costs only Odo and Frontier's file operations.

### Globus

Globus Connect Personal is not needed, and VISTA runs no Globus endpoint of its own. File
contents move over HTTPS `GET`/`PUT` straight against each cluster's own Globus collection,
authorized by the researcher's token; directory listings and `mkdir` use the Globus Transfer
API. There is no second collection for VISTA to own, install, or keep running.

Both OLCF collections are High Assurance, with a 3-day authentication timeout that refreshing a
token does not reset, and a Frontier queue wait can exceed it. When that happens VISTA reports
an expired session and asks the researcher to reconnect; it never reports it as an empty output
directory. The implementation notes are in
[`lib/globus.py`](../mcp_servers/vista_mcp_server/src/vista_mcp_server/lib/globus.py).

## Remote directories

Each researcher also sets, per cluster, a **remote directory** that names where VISTA puts job
sources and outputs. There is no default: where a project keeps its files is specific to the
project and the filesystem. On the OLCF clusters (Odo, Frontier, Lux) VISTA uses folders beside
it, and never creates the directory itself:

```
<remote dir>.<user>.jobs/<job>/src/  your sources, uploaded by VISTA; the job only reads them
<remote dir>.out/log-<id>.out        Slurm stdout, with log-<id>.err beside it
<remote dir>.out/<id>/               the job's outputs, exported to it as $VISTA_OUT
<remote dir>.out/<job>/              state a job's runs share, exported as $VISTA_JOB_DIR
```

`<user>` is your account on that cluster: Globus reports it on Odo and Frontier, and Lux takes it
from your SSH login. On Perlmutter the same folders live inside the remote directory instead, as
`<remote dir>/jobs/...` and `<remote dir>/out/...`, because Perlmutter jobs run as you and VISTA
manages the files through NERSC's IRI filesystem API. Every cluster's paths follow from the
remote directory and the job id alone, so VISTA finds a job's files again from its id, after a
restart or from another install sharing the directory.

Why OLCF splits them: each folder is created by the only identity that writes to it. VISTA
uploads your sources through your Globus identity (SFTP on Lux), so `.<user>.jobs` belongs to you,
one per researcher. Odo and Frontier jobs run as the project's IRI automation user, and Slurm
creates `.out` for their logs as that user. So the folder holding the remote directory must be
writable by the project's group. OLCF's `proj-shared` already is, so a new remote directory directly
under it needs no setup. Anywhere else, create the output folder once with
`mkdir -p -m 2775 <remote dir>.out`. VISTA checks before submitting and gives that command if it is
needed. Every job then keeps `.out` writable by the project's group (`umask 002`, plus `chgrp` and
`chmod 2775` when it owns the folder), so colleagues who set the same remote directory, and your
Lux and Frontier jobs if you give both the same one, can all write there. This is a temporary
workaround: S3M tokens cannot use the IRI filesystem API yet, which would let VISTA create one
folder as the automation user.

## Operational behaviour

VISTA keeps no record of submitted jobs. Status, outputs and cancel take the cluster that
`submit_hpc_job` reported, and changing a remote directory loses sight of the jobs under the old
one.

Job logs are tailed incrementally while a job runs: VISTA asks for the file's size, then reads
only what is new since the last poll.

To start VISTA without the job tools at all, set `VISTA_MCP_DISABLE_SERVERS=submit_job`. The
thin macOS developer app disables live submission by default; see
[development.md](development.md#the-thin-macos-developer-app).
