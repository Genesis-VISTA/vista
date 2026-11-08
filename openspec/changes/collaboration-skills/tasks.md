## 1. Backend

- [x] 1.1 `SkillTable`: drop `is_public` and `author`; add `bundled`, `origin_server`, `remote_id`, `remote_version`, `modified`; drop `author`, `repo_url`, `is_public` from `SkillCreate` / `SkillUpdate`
- [x] 1.2 Seeded and synced bundled skills are `bundled`; every write path sets `modified` (`build_skill_row`, `update_skill`, overwrite from a repository, replace from project output); the import paths refuse to replace only a bundled skill; delete is allowed for any skill
- [x] 1.3 `services/skill_collaboration.py`: `pack_skill` / `unpack_skill` (through `import_skill_from_files`), `SkillManifest`, `publish_skill` (new item, next version, `on_top_of`, `as_new`, conflicts), `list_remote_skills`, `import_skill` (new, renamed, in place, `replace_modified`, digest check)
- [x] 1.4 Routes `POST /skills/{name}/publish`, `GET /collaboration/skills`, `POST /collaboration/skills/{id}/import`; app handlers for `CollaborationError` (503/502) and `SkillConflict` (409), each `{detail, code}`
- [x] 1.5 `metrics_report.py` counts skills shared through a collaboration server by `remote_id`
- [x] 1.6 Tests: `tests/test_skill_collaboration.py` (archive, publish, conflicts and choices, listing, import against the in-process server), and the updated skill import, repository import, sync and metrics tests

## 2. UI

- [x] 2.1 `lib/types.ts` and the skill proxies carry the sharing fields; proxies for publish, the listing and import
- [x] 2.2 `lib/skill-sharing.ts`: listing, publish and import calls with their refusals, `sharingLabel`, `importAction`
- [x] 2.3 `/skill-hub` lists the server's skills (off with the reason without a server): Import, Import as…, Update (with the replace warning), Load
- [x] 2.4 `/skills`: loaded and other local skills, state line, Publish with the first-publish confirmation and the conflict choices (`PublishConflictModal`), Update, Load, Delete; unloading no longer deletes
- [x] 2.5 The skill editor loses author, repository URL and Save & publish; import dialog wording follows `bundled`
- [x] 2.6 Tests: `tests/skill-sharing.test.tsx`, `tests/SkillEditorModal.test.tsx`; hermetic fixtures

## 2b. Review round 1

- [x] 2b.1 One install-and-swap tail (`commit_skill_files`, `replace_files`, `invalidate_loading_projects`, `is_name_clash`); the output import now rebuilds every project that loads the skill
- [x] 2b.2 Executable bits survive an import; downloads capped; bundled skills not deletable; new imports named after the item; only a unique-name failure is `name_taken`
- [x] 2b.3 Listing degrades per item and has `manifests=false` for `/skills`; unchanged republish is a no-op; an edit during publish or import is not lost; manifest `repo_url` http(s) only
- [x] 2b.4 Hub: Revert for a modified copy at head, Load for any copy; `conversation-report` delta

## 3. Docs and checks

- [x] 3.1 `docs/skill-onboarding.md`, `AGENTS.md`, `README.md`, `collaboration_server/README.md`
- [x] 3.2 `./scripts/ci-local.sh backend collab ui` and `openspec validate --all`
