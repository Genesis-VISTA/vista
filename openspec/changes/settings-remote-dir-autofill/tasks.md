## 1. The fill rule

- [ ] 1.1 Add `ui/lib/remote-dir.ts` with `projectFolder`, `suggestedRemoteDir`, `isFilledRemoteDir`, `remoteDirFill` and `filledNote` (design.md D2). Verify with table-driven cases in a new `ui/tests/remote-dir.test.ts`:
  - `gen150-vista` → `gen150`
  - `CHM243` → `chm243`
  - empty → null
  - `-x` → null
  - an empty field fills
  - VISTA's folder for another project refills
  - VISTA's folder for the same project gives null
  - a typed path gives null
  - a trailing slash still counts as filled
  - Lux uses the Frontier template
  - each note string matches design.md D4
  - no note when the value differs from the suggestion

## 2. Settings modal

- [ ] 2.1 In `ui/components/UserSettingsModal.tsx`, fill Odo, Frontier and Lux remote directories from `useClusterState(cluster).checks.credential.project`, through the autosave path with `how: "now"`. Skip a field with a pending edit or focus (D1, D3). Verify in `ui/tests/UserSettingsModal.test.tsx` with a mocked cluster status:
  - saving an Odo token whose status reports `gen150-vista` saves `/gpfs/wolf2/olcf/gen150/proj-shared/vista` once and announces "Odo remote directory saved."
  - saving a Lux token whose status reports `stf218` fills `/lustre/orion/stf218/proj-shared/vista`
  - opening the modal with a token already saved and an empty directory fills it
  - a typed directory is never written
  - a pending edit is not overwritten
  - a hidden cluster is not filled
- [ ] 2.2 Show the note under the field while it holds the filled value, and add the "Filled from your S3M token once you add one." hint prefix to all three. Remove the "does not guess" sentence from `SavedField`'s comment. Verify in `ui/tests/UserSettingsModal.test.tsx` that the note text matches each spec scenario, disappears after an edit, and that the hint prefix shows before a project is known.

## 3. Checks

- [ ] 3.1 Run `./scripts/ci-local.sh ui lint` and `./scripts/ci-local.sh ui test`, and confirm both pass. (These run `next build` into `ui/.next`, so stop any `./launch.sh` stack in this checkout first.)
- [ ] 3.2 Manual, not in PR CI, needs a real S3M token: in a dev stack, paste an Odo token from a dashed subproject and check that the directory and its note match the storyboard (Part 4, frame H). Then check that a Frontier job submits with the filled directory. Record the result here.
- [ ] 3.3 Run `openspec validate settings-remote-dir-autofill` and confirm it passes.
