# forge-tune

Distributed fine-tuning of the FORGE foundation model on molten salt thermophysical property data. Trains a regression head on top of a frozen or unfrozen FORGE LLM to predict melting points from salt composition strings.

Defaults (nodes / duration / resources) are cluster-specific and live in `cluster_defaults.json`. Supported clusters: Odo (OLCF, IRI compute + S3 output push), Frontier (OLCF, same), and Perlmutter (NERSC, via IRI).

## Training data

`Molten_Salt_Thermophysical_Properties.csv` is required and gitignored, so it is absent on a fresh clone. Put it in this directory on the machine running the MCP server — Vista inlines every non-metadata file here into the JobSpec, so that is all the staging there is. Do not copy it to the cluster by hand: the remote run dir is namespaced by a session id that changes on every MCP server restart.

## Output directories

The job scripts split the two by size:

- `--log-dir $VISTA_OUT` — `training_speed_log.csv`, `gpu_memory_log.csv`.
  Uploaded when the job exits, so retrievable with `get_hpc_job_outputs`.
- `--checkpoint-dir $VISTA_KEEP` — `checkpoint_latest.pt`, `checkpoint_best.pt`,
  the periodic `checkpoint_epoch_*.pt`, and `<model>_classical_classifier.pt`.
  Kept on the cluster, never uploaded, not retrievable.

A checkpoint here is roughly 3x the model size (fp32 weights plus Adam's two
moment buffers), so ~17 GB for FORGE-S. That does not fit in the window the exit
trap gets when Slurm kills a job at its time limit, which is why these stay put.
`$VISTA_KEEP` lives outside the session directory so a later run can still
`--resume-from` a path from a previous MCP server session; nothing prunes it, so
facility purge policy on `proj-shared` is the retention story.

## Script args
TODO
