# forge-tune

Distributed fine-tuning of the FORGE foundation model on molten salt thermophysical property data. Trains a regression head on top of a frozen or unfrozen FORGE LLM to predict melting points from salt composition strings.

Defaults (nodes / duration / resources) are cluster-specific and live in `cluster_defaults.json`. Supported clusters: Odo (OLCF, via S3M), Frontier (OLCF, via IRI + SSH), and Perlmutter (NERSC, via IRI).

## Script args
TODO
