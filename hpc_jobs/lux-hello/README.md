# lux-hello

A minimal Lux job that checks VISTA can reach Lux and run a job there, without
training anything. It reports the compute node it ran on, the time, and the GPUs
`rocm-smi` sees, and writes the same to `hello.txt` in the job's output
directory.

Supported clusters: Lux only (OLCF, Slurm over SSH, project stf218; the user is
asked to log in through the hub once per session).

Default nodes: 1
Default time: 0:05:00 (it finishes in seconds)

Use it to confirm that sign-in, source upload, submission, status, and output
fetch all work before submitting a large job such as `forge-pretrain`.

## Script args
Arbitrary arguments allowed and are printed.

## Try it

```text
submit_hpc_job(job="lux-hello", cluster="lux")
get_hpc_job_status(job_id=<id>, cluster="lux")
get_hpc_job_outputs(job_id=<id>, cluster="lux")
```

Expected: a real Slurm job id, a completed state, and a `hello.txt` naming a Lux
compute node.
