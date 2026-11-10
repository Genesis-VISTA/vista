## Why

Every Odo, Frontier and Lux researcher has to type a remote directory before a job can run, and
the only help is a placeholder (`/gpfs/wolf2/olcf/<project>/proj-shared/vista`) that disappears
as soon as they start typing. VISTA already knows the project: the cluster card's credential
check introspects the cluster's S3M token (Odo's, Frontier's or Lux's) and reports its `project`
claim. It can fill the directory in rather than make the researcher work it out.

## What Changes

- When Settings has a cluster's project, it **fills that cluster's remote directory** with the
  project's `proj-shared/vista` folder. It does this when the field is empty, or when it still holds the
  folder VISTA filled for a different project. A directory the researcher typed is never
  changed.
- The folder's project is the claim **lowercased and cut at the first dash**: an S3M token from
  the subproject `gen150-vista` fills `/gpfs/wolf2/olcf/gen150/proj-shared/vista`. A live
  introspection (2026-11-06) showed the claim is the subproject (`gen150-vista`, automation user
  `gen150-vista_auser`) and carries no parent-project field. No project folder on orion or wolf2
  has a dash.
- A short note under the field says where the value came from while it is still the filled one:
  "Filled from your S3M token's project (gen150 folder inferred from gen150-vista)."
- The fill is an ordinary save: marked and announced like any other field.
- `SavedField`'s "VISTA does not guess" rule is retired.

Approved wording and screens: storyboard Part 4,
<https://claude.ai/artifact/RR2FxaXaoPvULbMxY6DNDG>.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `settings-modal`: adds a requirement that the remote directory is filled in from the
  cluster's project.

Related: `hpc-availability` (the credential check that reports the project) and the
`hpc-any-project` change (archived 2026-10-01), which made the directory a per-user setting.

## Non-goals

- No check that the filled folder exists (no Globus call). A wrong folder still shows on the
  cluster card's settings check.
- No warning when a directory the researcher typed isn't under the token's project.
- Perlmutter: its directory depends on the NERSC username, not the project.
- No backend, MCP or database change.
- No VISTAGuard work.

## Impact

- `ui/components/UserSettingsModal.tsx` (cluster fields, the fill, the note, hint copy)
- New pure module `ui/lib/remote-dir.ts` with tests in `ui/tests/remote-dir.test.ts`
- `ui/tests/UserSettingsModal.test.tsx`
