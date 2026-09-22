# refine-downscaling

REFINE 6x AI downscaling of daily Daymet `tmin`/`tmax`/`prcp` (1/4 degree ->
1/24 degree, ~25 km -> ~4 km) as GPU inference on OLCF Frontier, packaged as a
VISTA skill plus a curated `hpc_jobs/` entry on the existing `water4energy`
project.

**Status:** Proposed. Source package: `daliwang/Water4Energy_AgenticDemo`,
staged complete (code, data, weights, conda env) under
`/lustre/orion/world-shared/cli138/`. Authors: Haoran Niu, Deeksha Rastogi.
