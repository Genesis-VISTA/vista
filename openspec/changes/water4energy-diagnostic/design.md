## Context

`water4energy_diagnostic` is a self-contained package: one CLI program
(`plot_e3sm_era5.py`, 644 lines), pinned requirements, a bundled Natural Earth
coastline cache, a TVA service-area GeoJSON, and Frontier setup/run wrappers. It
was verified on Frontier `login04` on 2026-08-22 — **~37 s wall, 1 node, CPU only,
no GPU** — and it needs two gitignored NetCDF climatologies (1.1 GB ERA5 +
153 MB E3SM) that already live on Lustre.

The VISTA side it plugs into: skills are directories with a `SKILL.md`
(`agents/skills.py`), copied into per-skill storage and registered by `db/seed.py`;
HPC work is a curated `hpc_jobs/<name>/` entry dispatched by `submit_job_mcp.py`.
`salt-chemistry-md` is the closest precedent — clone-at-runtime code, nothing
vendored.

Two properties of this diagnostic make it unlike every existing catalog job:
its **inputs are pre-staged and read-only**, and it is **CPU-only and tiny**.

## Goals / Non-Goals

**Goals:**

- Run the existing diagnostic unmodified on Frontier via `submit_hpc_job`
- Emit structured metrics so the agent cites numbers instead of prose
- Give the agent enough domain guidance to judge whether a run is trustworthy
- Keep PR CI hermetic — no Frontier, no 1.25 GB of NetCDF, no network

**Non-Goals:**

- Generalizing variables / regions / file pairs (deferred; see proposal Non-goals)
- `dataset12/`, campaign manifest, VISTAGuard, vendoring the repo

## Decisions

1. **Clone at runtime; vendor nothing.**
   `github.com/daliwang/water4energy_diagnostic` is public (verified reachable,
   including the tarball endpoint). VISTA carries only `SKILL.md` and a thin job
   wrapper, matching `salt-chemistry-md`. Repo URL and ref are `cluster_defaults.json`
   env vars (`W4E_REPO_URL`, `W4E_REPO_REF`) so a fork or pinned SHA is config, not
   a code change.

2. **Self-healing venv at a fixed Lustre path.**
   `module load cray-python/3.11.7` + create-if-missing at
   `$W4E_ENV` (default under VISTA's `frontier_remote_dir`, i.e. the `chm243`
   project space VISTA already owns). Import-verify the seven packages; if the
   check fails, rebuild. Guard creation with `flock` so concurrent submissions
   don't race on the same prefix. The venv is *outside* `$VISTA_OUT` on purpose —
   `$VISTA_OUT` is per-job, and rebuilding 1.25 GB of wheels every run wastes the
   whole walltime budget.
   Rationale for self-healing over pre-provisioned: these are plain manylinux
   wheels through the OLCF proxy, not a ROCm conda build, so the cost of being
   wrong is ~3 minutes, not a support ticket.

3. **Resources and queue: `#SBATCH` headers are inert here.**
   `_submit_frontier_job` submits an IRI JobSpec (`executable: bash`,
   `arguments: ["-l","-c", job_cmd]`) with `job.frontier.slurm` inlined as the body,
   so `#SBATCH` lines are just comments. Walltime comes from
   `cluster_defaults.json` → `frontier.duration` (seconds); queue from
   `frontier.iri.queue_name`.
   On Frontier `debug` is a **QOS**, not a partition (partitions are `batch` and
   `extended`), and `IriAttributes` has `queue_name` + `custom_attributes` but no
   `qos` field.
   Chosen: `queue_name: "batch"`, `duration: 600`, and **no QOS plumbing**. Adding a
   `qos` passthrough to `IriAttributes` would mean touching shared MCP code for an
   optimization, not a requirement: a 1-node / 10-minute CPU job is prime backfill
   in `batch` regardless of QOS. This change therefore touches no shared dispatch
   code at all.

4. **Inputs referenced by env var, with a preflight check.**
   `W4E_DATA_DIR` in `cluster_defaults.json`, pointing at
   `/lustre/orion/lrn105/world-shared/wangd/water4energy_diagnostic`. The wrapper
   stats the three inputs before doing any work and fails with an actionable
   message naming the missing path.
   Rationale: VISTA's Frontier jobs charge the shared `chm243` account and run as
   a shared service identity, not as an `lrn105` member. Staging the climatologies
   under `world-shared` rather than `proj-shared` removes the cross-project read
   question entirely instead of relying on group permissions holding. If the path
   ever moves, this must still surface as one clear line — not a cartopy traceback
   — and the fix must be one config edit. Inputs are opened read-only and never
   written.

5. **`results.json` is produced by the wrapper, not by patching the science code.**
   `run_diagnostic.py` invokes `plot_e3sm_era5.py` unmodified and parses its
   printed summary into a stable schema, plus provenance (repo SHA, input paths
   and optional checksums, resolution, package versions, wall time, hostname,
   Slurm job id).
   Rationale: nobody on this side can land a PR on
   `daliwang/water4energy_diagnostic`, so the upstream program is a fixed
   dependency and the VISTA output contract belongs entirely to the wrapper.

   **Consequence, accepted deliberately:** the printed summary carries only
   `correlation`, `rmse`, and `bias` per variable per scope. Upstream's
   `weighted_performance_metrics` also computes the ERA5/E3SM means, the
   percent-normalized RMSE and relative bias, the σ ratio, and the cell counts —
   but those are rendered into the figure's metrics-table panel and never printed,
   so they are **not machine-readable**. The schema is shaped to absorb them
   without a version bump should a `--json-out` flag ever land upstream.
   A partial parse is a hard failure, never a `results.json` full of nulls.

6. **Offline-safe plotting.** Pass `--cartopy-data <clone>/cartopy_data` (the
   bundled Natural Earth 110 m coastline), `MPLBACKEND=Agg`, and `MPLCONFIGDIR`
   under `$VISTA_OUT`. The only network need is the first-run `pip install`, which
   works because VISTA already exports the OLCF proxy into the job env.

7. **Agent-side flow is fetch → display → interpret.**
   `get_hpc_job_outputs` for `results.json` and the PNGs, then `display_file` per
   figure. Metrics are read from `results.json`. `SKILL.md` carries the judgment
   rules so the agent does not present a broken run as a result.

8. **Offline tests only.** Catalog contract tests already parametrize over
   `hpc_jobs/`. New tests cover `SKILL.md` parsing, `cluster_defaults.json`
   validation, the `script_args` → argv mapping, stdout → `results.json` parsing
   against a captured fixture of the real Frontier output, and the seed
   registration of the skill + project.

## Risks / Trade-offs

- **Cold-environment first run exceeds a 10-minute budget.** First submission pays
  ~2–5 min of proxied wheel installs on top of the ~40 s run. Mitigation: the
  wrapper prints an explicit cold/warm env notice, and `SKILL.md` instructs the
  agent to submit the *first* run of a deployment with `duration="00:30:00"`,
  dropping to the 600 s default afterward.
- **Cross-project Lustre read (`lrn105` ← `chm243` identity).** Confirmed working;
  decision 4 makes a future break a one-line, clearly-diagnosed fix.
- **Stdout parsing is brittle to upstream format drift.** Mitigation: the parser
  fails loudly on an unmatched metric rather than emitting nulls, and the fixture
  test pins the format that shipped. There is no second source to fall back to —
  upstream is not ours to change — so drift means a failed job and a one-line
  regex fix, which is the intended trade.
- **Interpretation guidance is limited to r / RMSE / bias.** The σ ratio and
  nRMSE that a reviewer would naturally reach for are figure-only (decision 5), so
  SKILL.md must reason from the three machine-readable metrics and point at the
  figure for the rest.
- **Repo drift.** `W4E_REPO_REF` defaults to `main`; pin a SHA if reproducibility
  matters more than freshness.
- **Reference-value regression can't run in CI** (needs 1.25 GB of inputs). Caught
  only by the manual Frontier run in task group 6.

## Migration Plan

Additive throughout — no existing skill, job, or project changes behavior, and no
shared dispatch code is modified. Order: HPC job → structured metrics → `SKILL.md`
→ seed/project wiring → offline tests → manual Frontier validation → archive.

## Open Questions

- Is `batch` queue wait acceptable in practice for a 10-minute job? Only the manual
  run in task group 6 can tell. If the wait dominates, revisit a `qos` passthrough
  as a separate change against `submit_job_mcp.py` — deliberately out of scope here.
