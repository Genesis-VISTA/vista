## Why

Steps 3 and 4 (`collaboration-server`, `collaboration-skills`) made skills shareable through
the collaboration server, but a project that loads them can only be rebuilt by hand elsewhere. This is Step 5 of `PLAN.md`: projects are published
and imported, recursively over the skills they load.

## What Changes

- A project is an item of kind `project`. A version has a manifest and no blob. The manifest
  is built field by field from `ProjectPublic`: name, description, system prompt, tools, usage
  limits and forum URL. Each loaded skill is a reference, either an item and version on the
  same server or `bundled` by name. Knowledge bases are carried by slug.
- **BREAKING** `ProjectTable` gains `origin_server`, `remote_id`, `remote_version` and
  `modified`, which `ProjectPublic` also returns. A project starts modified. A save
  (`PUT /projects/{name}`) that changes its fields sets `modified`, as do loading a skill into
  it and the default-project sync adding a knowledge base. Publish and import clear it.
- `POST /projects/{name}/publish` (`{on_top_of?, as_new?}`) checks the project's own head,
  publishes the loaded skills that are not on the server or have changed, then publishes
  version 1 of a new item or the next version. A stale head is `409 version_conflict`, a lost
  item `409 remote_missing`, and a skill that needs a choice `409 skill_conflict` (naming
  it). A version whose manifest would match the one recorded is not published again.
- `GET /collaboration/projects` lists the server's projects. Each entry has its head's
  manifest, this installation's copy, and whether the name is taken here.
- `POST /collaboration/projects/{id}/import` (`{version?, rename_to?, replace_modified?}`)
  checks the project's fields and refusals, imports the referenced skill versions that are not
  here, then creates the project (with an id of its own) or updates the copy in place. Its
  result lists what happened to each skill; knowledge base slugs are kept as they are. A forum that cannot be
  reached does not fail the import: the project is created, and the reason comes back in
  `forum_error`.
- UI: each project card on `/projects` shows its state and has Publish, with the first-publish
  confirmation and the conflict choices. **Import from the collaboration server** opens a list
  of the server's projects with Import, Import as…, Update and Revert.
- No database migration: VISTA is in early development, so a database from before this
  change is deleted (`data/vista.db`) and recreated.

## Capabilities

### New Capabilities

- `project-collaboration`: publishing, listing and importing projects through the
  collaboration server, and how a project here records its state.

### Modified Capabilities

None. `skill-collaboration` (from `collaboration-skills`, not yet archived) is used as it
is.

## Non-goals

- Resolving knowledge bases: they are carried by slug, as they are (Step 6, `PLAN.md`).
- Publishing a project's conversations, debates or campaign runs.
- Choosing the name of a skill imported with a project: a taken name gets the first free
  `<name>-<n>`, and the result says so.
- Authentication or access control on the server. Deleting published versions.
- VISTAGuard.
