## Why

VISTA's skill catalog is entirely molten-salt / materials. The
`water4energy_diagnostic` package (public repo `daliwang/water4energy_diagnostic`)
is a working, Frontier-verified climate diagnostic: 1985–2014 annual-mean surface
temperature and precipitation, ERA5 vs E3SMv3, with a TVA Power Service Area
regional focus. Today a scientist runs it by hand on a Frontier login node.

Packaging it as a skill lets the VISTA agent submit the run, retrieve the figures,
and — where the real value sits — *interpret* the area-weighted metrics rather
than dumping four PNGs. It also demonstrates that the skill + `hpc_jobs` pattern
generalizes past molten salt, and it is the first catalog job whose inputs are
pre-staged read-only on Lustre instead of produced by the job.

## What Changes

- New curated HPC job `hpc_jobs/water4energy-diagnostic/` (Frontier only): clones
  the public repo at runtime, self-heals a cached `cray-python/3.11.7` venv, runs
  `plot_e3sm_era5.py` against pre-staged NetCDF climatologies, and writes the four
  figures plus a machine-readable `results.json` into `$VISTA_OUT`.
- New skill `db/skills/water4energy-diagnostic/SKILL.md`: trigger description,
  `script_args` contract, output contract, and interpretation guidance (what a
  degraded pattern correlation or bias means; why temperature nRMSE is omitted;
  which reference values signal a healthy run).
- New seeded project `water4energy` with its own system prompt.
- Structured metrics so the agent cites parsed numbers, never scraped prose.
- Hermetic tests only, plus a manual Frontier validation run outside CI.

## Capabilities

### New Capabilities

- `water4energy-diagnostic`: Frontier ERA5/E3SM climatology comparison delivered
  as a VISTA skill, HPC job, and seeded project.

### Modified Capabilities

- `hpc-job-contracts`: the catalog gains a CPU-only, short-duration Frontier entry
  whose inputs are pre-staged read-only on Lustre and whose Python environment is
  self-healing rather than pre-provisioned.

## Non-goals

- **No generalization** to arbitrary variables, regions, or climatology file
  pairs. That requires an additive upstream patch to `plot_e3sm_era5.py` and is
  deliberately deferred; the `script_args` surface is shaped to absorb it without
  a breaking change.
- No `dataset12/` Dataset 1 / Dataset 2 consistency checks.
- No campaign manifest — this is a standalone skill, not a fannable sim-skill.
- No VISTAGuard.
- No vendoring of the diagnostic repo into VISTA, and no `.gitignore` entry for
  the local working copy.
- No live / `hpc`-marked CI lane; no Frontier submission required to merge.

## Impact

- `hpc_jobs/` — new entry; catalog contract tests auto-parametrize over it
- `backend/src/vista_backend/db/skills/`, `db/system_prompts/`, `db/seed.py`
- `backend/tests/test_water4energy_skill.py` (new), seed snapshot tests
- No changes to `mcp_servers/vista_mcp_server` — the job uses the existing Frontier
  dispatch path as-is (see `design.md` decision 3)
