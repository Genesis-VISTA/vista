---
name: hello-lux
description: >-
  A first job on Lux: runs a small "hello" job on a compute node, then
  fetches and shows its report (host, GPUs, modules, and a checksum of the file VISTA
  uploaded). Use when the researcher wants to try, test or check their Lux
  connection, run hello-world on Lux, or run their first job there.
metadata:
  version: "0.1.0"
  tags: ["HPC", "Lux", "Hello"]
---

# Hello, Lux

This skill ships one job, `hello-lux/hello`, for Lux only. It follows the
`hpc-skill-author` job template, so it is also a worked example of a skill's job. It
needs what every Lux job needs: the Lux account and remote directory (set in Settings) and the researcher's RSA passcode at sign-in. Nothing else. It takes no `script_args`.

The job writes into its output folder: `hello.txt` (the report: host and node list, loaded
modules, what `rocm-smi` sees, start and end time, and the contents and `sha256sum` of the
`input.txt` shipped with the job, read from `RUN_DIR_Lux`), `summary.txt` and `run.log`.

## Run it

1. Submit it:
   ```text
   submit_hpc_job(job="hello-lux/hello", cluster="lux")
   ```
   Keep the `job_id` it returns.
2. Follow it with `get_hpc_job_status(job_id=<id>, cluster="lux")` until the state is
   terminal. Say where it is (queued, running) between polls; do not poll in a tight loop.
3. When it is `COMPLETED`, fetch the report:
   ```text
   get_hpc_job_outputs(job_id=<id>, files=["hello.txt"], cluster="lux")
   ```
4. Show the researcher `hello.txt`. Point out the host the job ran on, the GPUs, and that
   `input_sha256` is `642318411d1d65c88db402f0ae9b1a2964bf9772d17ae8b168633bd70bf5a197`, the checksum of the `input.txt` VISTA uploaded: it means
   the file arrived intact. If the job failed instead, show the log from the status call.

## When the cluster is not ready

If the submission is refused, no job exists. Say plainly which connection is missing, that
it is made in **Settings**, and **do not submit again until the researcher says the
connection is made.** Retrying a refusal only repeats it.

- **Lux remote directory not set.** `submit_hpc_job` refuses with "No Lux remote
  directory is set". No job was submitted. Tell the researcher it is set in **Settings**, and wait.
- **Lux account not set.** `submit_hpc_job` refuses with "No Lux account is set". No job
  was submitted. Tell the researcher the Lux account must be set in **Settings**, and wait.
- **RSA passcode.** Lux has no token: VISTA signs in over SSH and asks the researcher for
  their PIN and RSA passcode, once per chat session. If that prompt is declined, times out
  or the passcode is wrong, no job was submitted. Tell the researcher a passcode is needed
  to connect to Lux, and wait.

If a job fails or the report is missing for another reason, show the status and log as
they are and say what you see; do not guess at a cause.
