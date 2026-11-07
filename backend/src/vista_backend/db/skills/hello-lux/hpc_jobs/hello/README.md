# hello

A first job on Lux: it proves VISTA can upload a file, run a job on a compute node, and bring
the results back. Part of the `hello-lux` skill.

Supported clusters: Lux only.

Default nodes: 1
Default time: 0:05:00 (it finishes in seconds)

Needs a Lux S3M token, Globus connected for Frontier or Lux (one connection serves both), and the
Lux remote directory, all in Settings; nothing else.

## Script args

None. Any argument is refused.

## Outputs

- `hello.txt`: the report. The host and node list, the modules loaded, what `rocm-smi`
  sees, the input's contents and its `sha256sum` as read on the cluster, and the start
  and end time.
- `summary.txt`: the host, nodes, start and end times and the input's checksum.
- `run.log`: the full log (the same report).

`input.txt` is uploaded with the job. Its SHA-256 is `642318411d1d65c88db402f0ae9b1a2964bf9772d17ae8b168633bd70bf5a197`; `hello.txt` should report
the same.
