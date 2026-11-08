---
name: hello-odo
description: >-
  A first job on Odo: runs a small "hello" job on a compute node, then
  fetches and shows its report (host, GPUs, modules, and a checksum of the file VISTA
  uploaded). Use when the researcher wants to try, test or check their Odo
  connection, run hello-world on Odo, or run their first job there.
metadata:
  version: "0.1.0"
  tags: ["HPC", "Odo", "Hello"]
---

# Hello, Odo

This skill ships one job, `hello-odo/hello`, for Odo only. It follows the
`hpc-skill-author` job template, so it is also a worked example of a skill's job. It
needs what every Odo job needs: the S3M token and Globus, each connected for Odo, and the Odo remote directory. Nothing else. It takes no `script_args`.

The job writes into its output folder: `hello.txt` (the report: host and node list, loaded
modules, what `rocm-smi` sees, start and end time, and the contents and `sha256sum` of the
`input.txt` shipped with the job, read from `RUN_DIR_Odo`), `summary.txt` and `run.log`.

## Run it

1. Submit it:
   ```text
   submit_hpc_job(job="hello-odo/hello", cluster="odo")
   ```
   Keep the `job_id` it returns.
2. Follow it with `get_hpc_job_status(job_id=<id>, cluster="odo")` until the state is
   terminal. Say where it is (queued, running) between polls; do not poll in a tight loop.
3. When it is `COMPLETED`, fetch the report:
   ```text
   get_hpc_job_outputs(job_id=<id>, files=["hello.txt"], cluster="odo")
   ```
4. Show the researcher `hello.txt`. Point out the host the job ran on, the GPUs, and that
   `input_sha256` is `642318411d1d65c88db402f0ae9b1a2964bf9772d17ae8b168633bd70bf5a197`, the checksum of the `input.txt` VISTA uploaded: it means
   the file arrived intact. If the job failed instead, show the log from the status call.

## When the cluster is not ready

If the submission is refused, no job exists. Say plainly which connection is missing, that
it is made in **Settings**, and **do not submit again until the researcher says the
connection is made.** Retrying a refusal only repeats it.

- **Odo remote directory not set.** `submit_hpc_job` refuses with "No Odo remote
  directory is set". No job was submitted. Tell the researcher it is set in **Settings**, and wait.
- **Globus not connected for Odo.** `submit_hpc_job` refuses with "Globus file
  transfer is not connected for Odo". No job was submitted. Tell the researcher
  Globus must be connected for Odo in **Settings**, and wait.
- **No S3M token for Odo.** `submit_hpc_job` refuses with a message naming the
  missing token. No job was submitted. Tell the researcher the Odo connection must be
  made in **Settings**, and wait.
- **Expired Globus session.** Odo's collection requires a fresh Globus login every 3
  days, and a queue wait can outlast it. The job can finish while the session has expired,
  so fetching fails with a message that the session expired. The job did not fail, and its
  output folder is not empty. Tell the researcher to reconnect Globus for Odo in
  **Settings**, then fetch `hello.txt` again once they say it is done. Never report this as
  missing or empty output.

If a job fails or the report is missing for another reason, show the status and log as
they are and say what you see; do not guess at a cause.
