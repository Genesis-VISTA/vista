# Recorded IRI responses

Response bodies recorded from the live facilities on 2026-11-06, for the IRI client's
tests. `test_iri_fixtures.py` checks that every file parses and has the shape its name
says.

- `olcf_v2/`: S3M IRI v2 on Lux (`/api/v2/compute/...`), as Odo and Frontier answer on
  v2 too. One job that exited 7 (`submit`, `queued`, `failed_exit7`) and one canceled
  while queued (`canceled`; the cancel itself answered `204` with no body).
- `nersc_v1/`: NERSC IRI v1 on Perlmutter. Compute (`submit`, `queued`, `failed_exit7`),
  and finished filesystem tasks as `GET /api/v1/task/{id}` returned them.

What the facilities do that the files show:

- Job states are lowercase. A queued job reports `exit_code` 0, so an exit code means
  nothing until the job finishes. S3M sends the message `"None"` as a string, NERSC as
  `null`.
- `ls` names each entry by its resolved path (`/global/u1/...`) when asked for
  `/global/homes/...`, and lists the directory itself first.
- `download` returns a text file as raw text and a binary one as base64, with nothing to
  say which: compare the decoded length with `stat`'s size.
- A missing path is a `failed` task whose `error` contains `404`.

Sanitized: usernames, the project, home and project paths, hosts, and job and task ids are
replaced, and S3M's Slurm `meta_data` is cut to a few fields. Not recorded live, and built
from NERSC's `Task` and `TaskSubmitResponse` schemas instead: `task_submitted.json` and
`task_pending.json`. The download payloads are the bytes the probe uploaded, in the
encoding each came back in (the probe recorded only their lengths, and checked the
binary one byte for byte).
