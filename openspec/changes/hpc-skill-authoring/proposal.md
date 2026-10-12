# Proposal

## Why

Since `skill-hpc-jobs`, a loaded skill can ship its own HPC jobs at
`<skill>/hpc_jobs/<job>/`, submitted as `<skill>/<job>`. A researcher still cannot
get one out of a chat: "Save as skill" drafts only a SKILL.md body, the agent can
write files only to `/mnt/data/output` in its sandbox, and turning that folder into
a skill means downloading it and importing it by hand through /skills → Import →
Local folder. Nothing tells the agent the job contract either, so the jobs it
writes fail only at submission, one rule at a time.

## What Changes

- A bundled skill, `hpc-skill-author`, teaches the agent the job contract
  (README header, one `cluster_defaults.json` section per cluster, `job.<cluster>.slurm`,
  setup scripts, `VISTA_OUT` / `RUN_DIR_<Cluster>` / `VISTA_JOB_DIR`, a
  `KEY=VALUE` allow-list for `script_args`, writing only under `VISTA_OUT` and
  `VISTA_JOB_DIR`). It carries templates for a skill and a job, and
  `scripts/validate.py`, a standard-library script the agent runs in the sandbox
  that applies the checks `submit_hpc_job` applies (and the import's), plus the
  contract's advice as warnings. The agent writes the new skill to
  `/mnt/data/output/<skill>/`.
- The same skill wraps existing code: `scripts/get_source.py` fetches a public
  GitHub or GitLab repository (pinned to the commit its branch or tag points at), an
  uploaded archive or an uploaded folder and inventories it, and a `wrap-repo`
  template clones the repository on the cluster at that commit, so a
  researcher can say "make an HPC skill from <repo>" without reformatting it.
- "From project output" in the Import skill modal: lists the folders in the
  researcher's output folder for the active project that hold a SKILL.md, and
  imports the one they pick, through the same validation as a folder upload.
  The imported skill is private, is added to the project's skills (so the agent
  can submit its jobs from the next message on), and shows the existing
  "Runs HPC jobs" badge. Importing again replaces a private skill of the same
  name, so the agent can fix a skill and the researcher re-import it.
- Backend: `GET /projects/{project}/skills/from-output` (candidates) and
  `POST /projects/{project}/skills/from-output` (import), proxied by the UI.
- Docs: "Authoring a skill" and "HPC jobs inside a skill" in
  `docs/skill-onboarding.md`.

No breaking change.

## Capabilities

### New Capabilities
- `hpc-skill-authoring`: the bundled authoring skill and its validator, and
  importing a skill from the project's output folder into the project.

### Modified Capabilities
(none; `skill-hpc-jobs` is still an open change and is not modified)

## Impact

- `backend/src/vista_backend/db/skills/hpc-skill-author/` (new; registered at
  startup by `sync_bundled_skills`).
- `backend/src/vista_backend/agents/skill_import.py`, `services/skills.py`,
  `api/skills.py`, `agents/agents.py` (output path helper, skills volume staging).
- `ui/components/SkillImportModal.tsx`, `ui/app/skills/page.tsx`, new UI route
  under `ui/app/api/projects/[name]/skills/from-output/`.
- Tests: `mcp_servers/vista_mcp_server/tests/test_skill_author_validator.py`
  (validator agrees with `_load_job`; template skill resolves as `<skill>/<job>`),
  `backend/tests/test_skill_import.py` (import from output).
- No new dependencies.
