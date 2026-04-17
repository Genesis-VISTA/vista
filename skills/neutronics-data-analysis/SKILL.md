---
name: neutronics-data-analysis
description: Analyze SHIFT neutronics HDF5 output for FLiBe studies, including flux profiles, nuclide number-density surfaces, and interpolated tritium-production ratio maps over Li-6 enrichment and Be multiplier.
metadata:
  version: "0.1.0"
license: Proprietary
---

# Neutronics Data Analysis

Use this skill to post-process SHIFT output stored in `neutronics_isotopics.h5` with a processor class that loads core neutronics datasets, computes tritium production ratio, and generates analysis plots.

Assume all referenced scripts and assets already exist under this skill directory. Do not request script upload steps in the workflow.

## Primary Workflow

### 1. shift_example.py — End-to-End Analysis Driver (PREFERRED)
Runs the full analysis sequence from data load to plotting:
- Instantiates `ShiftFlibeProcessor` from `shift_processor.py`
- Loads required HDF5 fields into NumPy arrays
- Computes tritium ratio immediately via `compute_tritium_ratio()`
- Produces three plots:
  - `flux_vs_enrichment.png`
  - `nuclide_density.png`
  - `tritium_ratio_interpolated.png`

Example:
```bash
MPLBACKEND=Agg python3 /mnt/skills/neutronics-data-analysis/scripts/shift_example.py
```

Use this when the user asks for:
- "analyze SHIFT neutronics output"
- "plot flux vs enrichment"
- "plot number density surface"
- "plot tritium ratio"
- "run the full neutronics workflow"

### 2. shift_processor.py — Programmatic API for Custom Queries
Use this API when users want targeted interpolation or index lookups without running the full example workflow.

Core methods:
- `compute_tritium_ratio()`
- `tritium_ratio_interp(li6_enrichment, be_multiplier)`
- `flux_interp(cell_coordinate, li6_enrichment, be_multiplier)`
- `zaid_cell_interp(zaid, cell_coordinate, li6_enrichment, be_multiplier)`
- `nuclide_index(zaid)`
- `cell_index(cell_coordinate)`

## Execution Flow Details

`shift_example.py` imports and instantiates `ShiftFlibeProcessor("neutronics_isotopics.h5")`. During `__init__`, the processor reads these required datasets from HDF5 into arrays:
- `num_cells`
- `num_nuclides`
- `num_enrichments`
- `num_multipliers`
- `source_strength`
- `irradiation_time`
- `cell_edges`
- `beryllium_multipliers`
- `lithium6_enrichments`
- `nuclide_list`
- `cell_volumes`
- `flux`
- `number_density`

After loading, `compute_tritium_ratio()` runs automatically:
- Finds tritium ZAID (`1003`)
- Slices `number_density[tritium_idx, :, :, :]`
- Computes volume-integrated tritium atoms for each `(Li-6 enrichment, Be multiplier)` pair using dot-products with `cell_volumes`
- Normalizes by total incident neutrons:
  - `source_strength * irradiation_time`
- Stores a 2D `tritium_ratio` field

Then `shift_example.py` executes three analysis paths:
1. Direct-array flux plotting: `flux[:, idx, multiplier_idx]` vs cell centers from `cell_edges` -> `flux_vs_enrichment.png`
2. ZAID/cell lookup + number-density surface: `number_density[zaid_idx, cell, :, :]` -> `nuclide_density.png`
3. Interpolated tritium-ratio surface: dense grid through `tritium_ratio_interp()` (backed by `RegularGridInterpolator` over `lithium6_enrichments` and `beryllium_multipliers`) -> `tritium_ratio_interpolated.png`

## Practical Caveats

- `nuclide_index()` relies on list lookup; missing ZAIDs can raise exceptions unless guarded.
- Interpolation calls (`tritium_ratio_interp`, `flux_interp`, `zaid_cell_interp`) can fail on out-of-bounds input unless bounds handling is implemented.
- Ensure the HDF5 schema includes all required datasets listed above before running.

## Inputs and Outputs

Inputs:
- `/mnt/skills/neutronics-data-analysis/assets/neutronics_isotopics.h5`

Outputs:
- `/mnt/data/output/` (recommended output root)
- Expected plot files:
  - `flux_vs_enrichment.png`
  - `nuclide_density.png`
  - `tritium_ratio_interpolated.png`

## Agent Guidance

- Prefer `shift_example.py` for full user-facing analysis requests.
- Prefer `shift_processor.py` APIs for targeted programmatic questions (single ZAID, off-grid interpolation, parametric scans).
- Use `MPLBACKEND=Agg` for non-interactive plotting environments.
- Always surface generated plot paths in the final response so the UI can render artifacts.
