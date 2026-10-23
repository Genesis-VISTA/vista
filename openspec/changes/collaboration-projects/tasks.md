## 1. Backend

- [x] 1.1 `ProjectSharing` (`origin_server`, `remote_id`, `remote_version`, `modified`) on `ProjectTable` and `ProjectPublic`, not `ProjectCreate`; `project_content`; `create_project`, `update_project` (only on a real change), `load_into` and `sync_default_projects` set `modified`
- [x] 1.2 `services/collaboration.py`: `list_with_heads`, used by `list_remote_skills`; `skill_collaboration.reread` and `linked_skill` public
- [x] 1.3 `services/project_collaboration.py`: `ProjectManifest`, `SkillRef`; `publish_project` (head check, skills first, unchanged, `on_top_of`, `as_new`, conflicts); `list_remote_projects`; `import_project` (project checks first, skills imported / updated / kept / missing, free skill names, present knowledge bases, new id, in place, forum after commit)
- [x] 1.4 Routes `POST /projects/{name}/publish`, `GET /collaboration/projects`, `POST /collaboration/projects/{id}/import`; `ProjectConflict` shares the 409 handler
- [x] 1.5 Tests: `tests/test_project_collaboration.py` against the in-process server (the forum test needs git)

## 2. UI

- [x] 2.1 `Project` carries the sharing fields (`ProjectSharing`, `ProjectFields`); proxies for publish, the listing and import
- [x] 2.2 `lib/project-sharing.ts`: listing, publish and import calls, `projectSharingLabel`, `projectImportAction`, `importSummary`
- [x] 2.3 `/projects`: state line and Publish on each card, first-publish confirmation, conflict choices (`PublishConflictModal` now takes `name` and `noun`), `skill_conflict` pointing to `/skills`
- [x] 2.4 `ProjectImportModal`: the server's projects with Import, Import as…, Update, Revert and the replace confirmation; the result's summary on the page
- [x] 2.5 Tests: `tests/project-sharing.test.tsx`; fixtures carry the sharing fields

## 2b. Review round 1

- [x] 2b.1 Knowledge base slugs and absent bundled skill names carried on import; an import that differs from its version (kept or left-out skills) is modified; "unchanged" compares manifests without display names, with blank text and usage limits normalised
- [x] 2b.2 A skill's conflict during a project import is `skill_conflict`; project fields validated before any skill; free skill names picked locally; blank text fields stored as none
- [x] 2b.3 `SharingConflict` base and one handler; seeded projects `modified`; "Use the server's version" sends the head shown (skills and projects); the rename prompt keeps the name typed
- [x] 2b.4 Tests in `tests/test_project_collaboration.py` and `tests/project-sharing.test.tsx`

## 2c. Review round 2

- [x] 2c.1 "Unchanged" compares only the skills loaded (`_skill_keys`), relying on `modified` for the project's own fields; `_identity` and its normalisation removed
- [x] 2c.2 A bundled name this computer lacks is published as bundled again when the version built on referenced it so
- [x] 2c.3 A modified project "differs here"; the import offers "Re-import version N"

## 2d. Review round 3

- [x] 2d.1 A kept skill copy no longer makes the imported project modified; only a bundled skill shadowed here does
- [x] 2d.2 `SkillRef.name` must match `SKILL_NAME_PATTERN`; the imported project's name must match the projects page's rule (`name_invalid`, `400` for a typed one, `projectNameError` in the dialog)
- [x] 2d.3 The skill catalog is refetched after an import; the import action's kind is `reimport`

## 3. Docs and checks

- [x] 3.1 `docs/project-onboarding.md`, `collaboration_server/README.md`, `README.md`
- [x] 3.2 `./scripts/ci-local.sh backend ui` and `openspec validate --all`
