# Tasks

## 1. Authoring skill

- [x] 1.1 Add `backend/src/vista_backend/db/skills/hpc-skill-author/` with SKILL.md (contract, workflow, how to hand off to the researcher) and `templates/` (SKILL.md and a Lux job); verify `read_skill` parses it and `sync_bundled_skills` would register it
- [x] 1.2 Write `scripts/validate.py` (standard library only; errors and warnings as in the spec; `--job` for one job folder; `--json`); verify it exits 0 on the templates and 1 on a broken job

## 2. Validator parity

- [x] 2.1 Add `mcp_servers/vista_mcp_server/tests/test_skill_author_validator.py`: valid and broken fixtures agree with `_load_job`; every built-in and shipped job passes; a skill made from the templates validates and resolves as `<skill>/<job>`; verify with `uv run --extra dev pytest tests/test_skill_author_validator.py`

## 3. Import from output

- [x] 3.1 Add `agent_volume_root()` and `stage_skills()` in `agents/agents.py` and use them in `ProjectAgent`; verify existing agent tests pass
- [x] 3.2 Add `import_skill_from_dir()` in `agents/skill_import.py` (no symlinks, lazy files, reuses `import_skill_from_files`); verify unit tests for symlink refusal and a nested job
- [x] 3.3 Add the list/import service and `GET`/`POST /projects/{project}/skills/from-output` (private, attach to project, invalidate agents, opt-in replace of a private skill); verify backend tests: import, load (staged volume holds the job), 409, replace, published not replaced, symlink refused, other user's output not visible

## 4. UI

- [x] 4.1 Proxy route `ui/app/api/projects/[name]/skills/from-output/route.ts` and a "From project output" tab in `SkillImportModal.tsx` (list, pick, import, replace on 409); refresh the active project after import; verify `npm run lint` and `tsc`

## 5. Wrapping existing code

- [x] 5.1 Add `scripts/get_source.py` (GitHub or GitLab URL pinned to a commit, archive or folder; safe unpacking) and `scripts/inventory.py`; verify `tests/test_skill_author_wrap.py`
- [x] 5.2 Add the `wrap-repo` template (setup clones the pinned commit once on the Lux login node) and the "Wrapping existing code" section of SKILL.md; verify the wrapped skill validates and resolves, setup checks out the pinned commit once, and the job script refuses unknown args
- [x] 5.3 Document wrapping in `docs/skill-onboarding.md`
- [x] 5.4 Move repository fetching to the backend: `agents/source_fetch.py` and the agent's `fetch_repository` tool (GitHub/GitLab APIs only, pinned commit, optional `VISTA_BACKEND_GITLAB_TOKEN`), `get_source.py` offline-only; verify `backend/tests/test_source_fetch.py`

## 6. Software environments

- [x] 6.1 Add "Software environment" to SKILL.md (prefer a pre-built env; proxy, OLCF docs, off-Lustre caches, stripe count 1) and `templates/conda-env/build_conda_env.sh`; warn in the validator on a conda build without `CONDA_PKGS_DIRS`; verify the build-once and warning tests in `tests/test_skill_author_wrap.py`

## 7. Docs and CI

- [x] 7.1 Update `docs/skill-onboarding.md` ("Authoring a skill", "HPC jobs inside a skill") and link from `docs/hpc-jobs.md`; verify the link check in `ci-local.sh`
- [x] 7.2 Run `./scripts/ci-local.sh backend mcp ui` and verify it passes; `openspec validate hpc-skill-authoring --strict`
  (2026-10-11: all jobs pass except `ui:browser`, which needs `npx playwright install chromium` on this machine,
  and `test_environment_path_is_unchanged`, which fails only because a `.env` above the worktree sets a model)
