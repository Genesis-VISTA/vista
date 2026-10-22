## Why

Step 3 (`collaboration-server`) built a server of items and numbered versions, and a backend
client, with nothing published yet. The old Skill Hub was a local flag (`is_public`, one-way,
undeletable) that shared nothing: it filtered this computer's own list. This is Step 4 of
`PLAN.md`: skills are published to and imported from the collaboration server, and the Skill
Hub lists the server's skills.

## What Changes

- A skill is an item of kind `skill`. A version holds the skill's folder as a gzipped tar
  and a manifest read from its SKILL.md frontmatter, plus its HPC jobs, size and `repo_url`.
  Imports unpack through the folder-upload path and its checks.
- **BREAKING** `SkillTable` loses `is_public` and `author`, and gains `bundled`,
  `origin_server`, `remote_id`, `remote_version` and `modified`; `repo_url` stays. Bundled skills are marked `bundled`. The one-way publish
  and no-delete rules go: only a bundled skill is kept from deletion and from being replaced
  by an import. `SkillCreate` and `SkillUpdate` lose `author`, `repo_url` and `is_public`.
- Every write to a skill's files (create, `PATCH`, a replace from project output or a
  repository) sets `modified`; publish and import clear it.
- `POST /skills/{name}/publish`: version 1 of a new item, or the next version on top of
  `remote_version`. A stale head is a `409` with `code: version_conflict` and the head, after
  which the researcher takes the server's version (import with `replace_modified`), publishes
  on top of the head (`on_top_of`), or publishes as a new skill (`as_new`). Bundled skills are
  not published.
- `GET /collaboration/skills` lists the server's skills with their head's manifest and this
  installation's copy; `POST /collaboration/skills/{id}/import` imports a version: the copy
  from the same item is updated in place (`409 modified` first if it has local changes), and
  a taken name is `409 name_taken` until the researcher gives `rename_to`.
- UI: `/skill-hub` lists the server's skills (off, with the reason, without a server) with
  Import, Update and rename; `/skills` shows each skill's state, Publish with the conflict
  choices, and a local library (load, delete) now that the hub no longer lists it. The
  skill editor's free-text author, repository URL and "Save & publish" go.
- No database migration: VISTA is in early development, so a database from before this change
  is deleted (`data/vista.db`) and recreated.

## Capabilities

### New Capabilities

- `skill-collaboration`: publishing, listing and importing skills through the collaboration
  server, and how a skill here records its state.

### Modified Capabilities

- `hpc-skill-authoring`: an import from project output may replace any skill but a bundled
  one, and marks it modified.
- `conversation-report`: the skill editor saves only; publishing is on the Skills page.

## Non-goals

- Projects (Step 5) and knowledge bases (Step 6), including references from a project to a
  skill version (`bundled:<name>`).
- Reviewing imported skills: importing is the researcher's trust decision.
- Deleting or withdrawing a published version; garbage-collecting blobs.
- Authentication or access control on the server.
- A listing that carries manifests itself: the backend asks for each head (see design).
- VISTAGuard.
