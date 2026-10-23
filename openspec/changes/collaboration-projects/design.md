## Context

The server (`collaboration-server`) stores items of any kind with numbered, immutable versions
and a compare-and-set on `parent_version`. Skills (`collaboration-skills`) are items of kind
`skill` with a tar.gz blob. `publish_skill` and `import_skill` handle a skill's conflicts, and
each ends its own transaction. A project row is a handful of fields plus the names of the
skills it loads and the slugs of the knowledge bases it searches. The seeded default projects
have fixed ids that every installation shares.

## Decisions

### D1: A project version is a manifest, built field by field

`ProjectManifest` (`format: 1`) holds name, description, system_prompt, tools, usage_limits,
forum_repo_url, `skills` and `knowledge_bases`. Each field is copied from `ProjectPublic` by
name (`_build_manifest`), so nothing the table later gains (ids, collaboration state) can
reach the server by accident. There is no blob. A version in another format, or one that does
not validate, is listed without a manifest and is not imported.

A skill reference is `{name, bundled: true}` for a bundled skill, which is PLAN.md's
`bundled:<name>`, or `{name, remote_id, version}`. `name` is the publisher's, for display.
A bundled skill is referenced by name even when it was edited here: bundled skills are never
published, so the importer gets their own copy.

Knowledge bases are slugs, carried as they are and not resolved (Step 6), which will
redefine them as references, with a format bump.

### D2: Publishing checks the project's head, then publishes skills, then the project

1. If the project has an item on this server, its head must equal the parent (`remote_version`
   or `on_top_of`). Otherwise the publish is refused before anything is sent, so a conflict
   on the project does not publish skills first.
2. Every loaded skill that is not bundled goes through `publish_skill`. That call does nothing
   for a skill published to this server and unchanged since. It publishes a version for a
   changed skill, and a new item for one never published here. A skill conflict becomes the
   project's `409 skill_conflict`, carrying `skill` and `skill_code`, and is resolved on the
   Skills page. A loaded skill that is not on this computer is referenced as bundled again
   when the version built on referenced it so (a bundled skill of another VISTA, kept by the
   import); any other is a `400` naming it. Only a linked publish has a version to build on:
   publishing such a project `as_new` (after `remote_missing`, say), or on top of a head that
   no longer names the skill, is the `400`, and the researcher removes it first. Rare, as it
   needs a bundled skill missing here and the item gone or changed.
3. The manifest is built from the project as it was read at the start.
4. If the project is unchanged (`modified` false, no `on_top_of`) and it loads the same skills
   as the recorded version (`_skill_keys`: a bundled skill by name, any other by item and
   version, so a skill renamed on import still matches), nothing is published
   (`status: unchanged`). Only the skills need comparing: `modified` false already means the
   project's own fields are that version's, since every write to them sets it, a publish
   clears it only if they did not change meanwhile, and an import writes them from that
   version. The project's own fields may not have changed while a new skill version did,
   which is why the flag alone does not decide. The recorded version is fetched once, with
   the head check.
5. Publishing is the compare-and-set. It can still lose to a publish in between, and then the
   skills stay published.

Because the head check comes first, a project unchanged here but behind the server's head is
a `version_conflict`, not `unchanged` as a skill would be. Telling the two apart would mean
publishing its skills first; and the researcher is better served by the choice, since the
card already says a newer version is available.

`modified` is cleared only if `project_content` (the `ProjectCreate` fields) is unchanged
after the server answered. The row is read again in a new transaction, as for a skill.

### D3: What modifies a project

`create_project` sets `modified` (all of a new project is local). `update_project` sets it
only when `project_content` changes, since the UI saves the whole project to load one skill.
`load_into` sets it, and so does `sync_default_projects` when it adds a knowledge base. A
skill edited after its project was published does not set the project's flag. The skill's
own `modified` records that, and the project's next publish picks it up (D2.4).

### D4: Import checks the project first, then skills, then writes the row

The project-level refusals are checked before any skill is imported: `modified` (a copy from
the same item with local changes, without `replace_modified`) and `name_taken` (a different
project with the name). A copy at that version, unchanged, is `unchanged` and nothing happens.

Then, for each reference:

- bundled, and bundled here: `unchanged`. Not here at all: `missing`, and the project still
  names it, so the next version does not lose it: a publish from here references it as
  bundled again (D2.2). A different, non-bundled skill here with the name: `missing`, and left out,
  as the project would otherwise load the wrong skill.
- no copy of the item here: imported as a new skill, named after the reference. A taken name
  gets the first free `<name>-<n>`, picked from the names here before the one import, since
  asking for each skill would make a project import a dialog per skill.
- a copy at that version, unchanged: `unchanged`.
- a copy older and unchanged: updated in place to that version (`import_skill`).
- a copy newer, or with changes here: `kept` as it is, and said so. Downgrading a skill or
  overwriting local work would change other projects that load it.

A skill's own conflict (a name taken by a racing write, a copy changed while it downloads)
stops the import as the project's `409 skill_conflict`, carrying `skill` and `skill_code`,
never under the project's own `modified` or `name_taken`, whose choices would not reach it.
Each skill import commits on its own. If the project then fails, the skills stay in the
library, which is harmless.

The project's fields are validated through `ProjectCreate` before any skill is imported, so a
version VISTA cannot store imports nothing. Blank text fields become none, as a save stores
them.

Knowledge bases: every slug is kept, as it is, whether or not a knowledge base of that slug
is here. That is "carried, not resolved": dropping the absent ones would drop them from every
version published from here. A project naming a knowledge base that is not here is a state
VISTA already has (deleting a knowledge base leaves its slug on projects).

A `kept` copy does not make the project `modified`. It is the skill's own state, as D3 has it
for a skill edited after its project was published, and the project's next publish publishes
that copy first and references it (D2.4), losing nothing. Only a reference the project here
loses sets `modified`: a bundled skill left out because a different skill here has its name.
A publish from here would drop it, and the card says the project differs. The UI words a
modified project neutrally: it "differs here from version N", the import offers "Re-import
version N", and the warning says it was changed here or does not load a skill that version
names.

Names from someone else's manifest are checked before they are used:

- every `SkillRef.name` must be a skill name (`SKILL_NAME_PATTERN`, kebab case). A bundled
  name may be kept on the project, and the agent joins it onto its skills folder, so a path
  there would reach outside it. A manifest that fails is not one this VISTA imports.
- the project's name must be one the projects page accepts (2 to 80 letters, digits, spaces,
  `.`, `_`, `-`), as it is the path segment of every project route. The item's name failing
  is `409 name_invalid`, so the dialog asks for another; a typed `rename_to` failing is a
  `400`. The dialog checks the typed name by the same rule (`projectNameError`) before
  sending it.

The project row is a new `ProjectTable` with a new `uuid4`, or the copy updated in place
(keeping its id and name, rebuilding its agents). A manifest's extra keys, an `id` included, are ignored, so an import never
takes a local id: the seeded projects share fixed ids, and `sync_default_projects` would
otherwise fight over them. An in-place update is refused (`modified`) if the row changed while
the skills were imported. A new project's name is checked again just before the insert,
because project names are unique only by that check.

### D5: The forum is set up after the commit, and its failure is reported

A save refuses a forum URL that cannot be reached (`_open_the_lab`), because the person who
typed it can fix it. An imported URL was typed by someone else and may be unreachable from
here. So `ensure_forum` runs after the project is committed, and a `ForumSetupError` is
returned as `forum_error`. The URL stays on the project, and the lab page reports a forum that
is not shared. In the usual case, where the remote was set and only the sync could not reach
it, the lab works once the remote answers. If the failure came before the remote was set (the
forum's folder could not be created), saving the project again sets it up.

### D6: Shared pieces

`list_with_heads` (`services/collaboration.py`) does the per-item head fetch for both
listings, and `list_remote_skills` now uses it. `reread` and `linked_skill` are now public in
`skill_collaboration`. `SkillConflict` and `ProjectConflict` share a base,
`collaboration.SharingConflict`, and its one 409 handler (`409 {detail, code, …}`). "Use
the server's version" imports the head the conflict reported (`version`), for skills and
projects alike, not whatever is newest by the time it is clicked. In the UI, `PublishConflictModal` takes `name` and `noun`, and
`lib/project-sharing.ts` reuses `refusal` and `post` from `lib/skill-sharing.ts`.

## Risks

- A project here may name knowledge bases this computer does not have; the agent is told of
  them and `rag_search` answers that the slug is unknown. Step 6 resolves knowledge bases.
- An imported forum URL is a git remote chosen by the publisher, and import contacts it. Git's
  own protocol policy applies (`ext::` is off by default). Importing is the researcher's trust
  decision, as for a skill.
- The checks in D2.1 and D4 do not lock anything: a racing publish or edit is caught by the
  compare-and-set or the re-read, after some skills may already have been published or
  imported.
