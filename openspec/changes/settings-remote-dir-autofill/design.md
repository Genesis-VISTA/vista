## Context

See proposal.md for why. The pieces already exist:

- Saving `odo_s3m_token` or `frontier_s3m_token` makes `saveField` call
  `recheckHpcStatus(cluster)` (`CREDENTIAL_CLUSTER`). Saving `lux_account` (in `SETTING_FIELDS`)
  calls `refreshHpcStatus()`.
- The backend's credential check (`services/hpc_status.py::_credential`) returns
  `checks.credential.project`: the token's introspected `project` for Odo and Frontier, and
  `local.lux_account` for Lux. The modal reads cluster state through `useClusterState(cluster)`,
  the same store the rail's cards use.
- Remote directories are debounced text fields bound with `bind(field, "debounce")`, which keeps a
  draft and goes through `useSettingsAutosave`.
- A hidden cluster is not rechecked, so it has no project, and this change does nothing for it.

Facts behind the folder rule, both checked on 2026-11-06:
- **The claim is the subproject.** A live introspection of an Odo token returned
  `project: "gen150-vista"`, `username: "gen150-vista_auser"`, and no parent-project field.
- **Folders have no dashes.** The researcher looked on orion and wolf2 and found no project folder
  with a dash. Ids seen in the repo are all lowercase (`gen150`, `chm243`, `stf218`).

## Goals / Non-Goals

**Goals:**
- One pure rule, tested on its own, deciding what a field should hold given the cluster, the
  project and the current value.
- No new state. Whether VISTA filled a value is decided from the value itself.

**Non-Goals:**
- No backend change. The fill does not happen in the save path or in the status check.
- No existence check and no mismatch warning (proposal Non-goals).

## Decisions

### D1: The fill runs in the Settings modal, from cluster status

When `useClusterState(cluster).checks.credential.project` changes, or the modal mounts with it,
the modal computes the suggestion and, if the rule says so, sets the draft and saves it through
the same autosave path as a typed edit with `how: "now"`. The mark and the announcement then come
for free.

Alternatives considered:
- **In the backend's save path.** It would add an S3M round trip to saving a token.
- **In the backend's status check.** A poll would then write settings in the background.

The researcher chose the modal (grilling, 2026-11-06).

### D2: Pure module `ui/lib/remote-dir.ts`

- `projectFolder(project)`: lowercase, cut at the first `-`. Returns null for an empty or unusable
  result, i.e. anything not matching `^[a-z0-9]+$` after the cut.
- `suggestedRemoteDir(cluster, project)`: the cluster's template with that folder, or null.
- `isFilledRemoteDir(cluster, value)`: whether a value matches the cluster's template for some
  folder. This is what "VISTA's folder for a different project" means:
  `^/gpfs/wolf2/olcf/[a-z0-9]+/proj-shared/vista/?$` for Odo, and
  `^/lustre/orion/[a-z0-9]+/proj-shared/vista/?$` for Frontier and Lux.
- `remoteDirFill(cluster, project, current) → string | null`: the value to write, or null to
  leave the field. The suggestion is returned when `current` is empty, or when it is a filled
  folder different from the suggestion.
- `filledNote(cluster, project, current) → string | null`: the note text when
  `current === suggestedRemoteDir(cluster, project)`, otherwise null.

**Why a template match over a stored "filled" flag:** no new column (`db/schemas.py` stays
untouched), and a researcher who types exactly VISTA's path is treated the same as a fill, which
is harmless.

### D3: Never race the researcher

The fill is skipped while the field has an unsaved edit (an autosave entry pending for it), or
while the field has focus. A project arriving mid-typing therefore never overwrites the typing.
The next status change or modal open tries again.

### D4: Copy

The wording is from the approved storyboard (Part 4):

- **Notes:**
  - "Filled from your S3M token's project (gen150 folder inferred from gen150-vista)."
  - "Filled from your S3M token's project, chm243."
  - "Filled from your Lux account, stf218."
- **Hint prefixes:**
  - "Filled from your S3M token once you add one." (Odo, Frontier)
  - "Filled from your Lux account once you add one." (Lux)

  Each is followed by the existing `GroupWritableHint`.

The note is a separate line under the field, in the success tint used by saved states, and it is
not part of the hint. The `SavedField` doc comment's "does not guess" sentence is removed.

## Risks / Trade-offs

- **[A project whose folder really has a dash]** The cut gives the wrong folder. → The note names
  both the claim and the folder, the cluster card's settings check reports an unusable directory
  at use time, and the researcher can type over it, which is never replaced.
- **[A token from another project while a typed directory points at the old one]** The job fails
  for permissions. → Out of scope (no mismatch warning). The card's checks surface it.
- **[Status arrives after the modal closes]** Nothing fills until the modal is opened again. This
  is acceptable, because researchers paste tokens with the modal open.

## Migration Plan

UI only; nothing to migrate. Existing installs with a token and an empty directory are filled the
next time that cluster's settings are open (spec scenario "A token saved before this change").
