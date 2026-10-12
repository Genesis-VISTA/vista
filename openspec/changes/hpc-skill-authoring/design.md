# Design

## Context

- The agent's sandbox mounts the project volume at `/mnt`; `/mnt/data/output` is
  `<data_dir>/volumes/<project-id>-<user-id>/data/output` on the host
  (`ProjectAgent.output_dir`). The backend can read it without starting an agent.
- The agent's skills volume (`/mnt/skills`) is rebuilt from the project's
  `skills` list when a `ProjectAgent` starts. The UI's per-browser "loaded"
  additions (localStorage) never reach it, so a skill whose jobs the agent must
  submit has to be in `Project.skills`.
- `submit_hpc_job` checks a skill job with `_load_job` when it is submitted, and
  refuses a cluster with no `cluster_defaults.json` section at dispatch. The
  sandbox has Python but not `vista_mcp_server` or, reliably, pydantic or PyYAML.

## Goals / Non-Goals

**Goals:** agent writes a skill that passes on the first submission; one click
from the researcher turns it into a loaded skill; same import validation as a
folder upload.

**Non-Goals:** the agent importing or loading skills by itself; a per-project
trust approval for skill jobs (still proposed in `skill-hpc-jobs`); editing a
skill's files in place from the UI; fixing the localStorage-only "loaded"
additions in general.

## Decisions

- **Researcher clicks, agent does not import.** Loading a skill lets the agent
  run its scripts on a cluster under the researcher's credentials. A backend
  agent tool would make that one prompt-injected tool call away; VISTA's tool
  approval path (`requires_approval`) has no tool using it yet. So the agent
  ends by telling the researcher to open Skills → Import → From project output.
  The modal lists only the researcher's own output folder for the active
  project, so the pick is one click, not a folder dialog.
- **Validator is standard library, and at least as strict as VISTA.** It
  re-implements `_load_job`'s rules, the dispatch-time "section for the
  cluster" rule, the catalog test's Lux partition/GPU rule, and the import's
  rules, with strict JSON types for `cluster_defaults.json` (an `int` must be a
  JSON integer). A parity test in the MCP server suite runs it against fixtures
  and against every built-in and shipped job, so it can never accept what
  `_load_job` refuses nor refuse a job VISTA ships. Contract advice that VISTA
  cannot check (`VISTA_OUT` use, an allow-list for `script_args`) is a warning.
  SKILL.md frontmatter is read with PyYAML when importable, else with a small
  parser for the `name`/`description` keys.
- **Templates live under `templates/`, not `hpc_jobs/`.** A folder under
  `hpc_jobs/` would make `hpc-skill-author` itself a skill that runs cluster
  jobs and badge it. The templates are a Lux job (the cluster most new work
  targets) and a SKILL.md, with the other clusters' differences in the SKILL.md.
- **Import from output reuses `import_skill_from_files`.** The folder is walked
  without following links; any symlink refuses the import. Files are opened
  lazily so the file-count cap applies before descriptors run out. Paths are
  prefixed with the folder name so the upload's single-top-folder stripping
  behaves the same for every layout.
- **Import attaches to the project and evicts its agents.** The endpoint adds the
  name to `Project.skills` (new list, for the JSON column) and calls
  `invalidate_agents`, so the next message's agent stages the skill. The
  volume staging loop moves to a function, `stage_skills`, so a test can check
  the staged layout without an agent.
- **Replace is opt-in and private-only.** A 409 for a private skill offers
  "Replace" in the modal; the backend copies the new folder to a fresh storage
  path, points the row at it, re-mirrors the frontmatter, and removes the old
  folder after commit. Published skills are never replaced, as they cannot be
  deleted.

- **Wrap, don't rewrite; clone repositories on the cluster, pinned.** A
  wrapped repository is cloned by the job's setup at the commit `fetch_repository`
  resolved, into `$VISTA_JOB_DIR/src/<commit>`, rather than copied into the
  skill: the skill stays a few small files under the import limits, the
  cluster gets the history and submodules as the code's own docs expect, and a
  moving branch cannot change what an imported skill runs. On Lux that happens
  in `setup_lux.sh` on the login node (compute nodes cannot clone); on Odo and
  Frontier the job script clones, as `alloy-thermo-mc` does.
- **The backend fetches repositories, the sandbox never does.** The sandbox
  holds no credentials (the agent can read everything in it) and, on ORNL's
  network, does not trust the Netskope proxy that re-signs HTTPS, which a first
  test hit. The backend has both, as it does for **Import → GitHub URL**. So the
  agent calls `fetch_repository(url, ref?)`, a backend tool that pins the ref
  to a commit and unpacks the archive into the agent's `/mnt/data/work/`, and
  the sandbox reads it offline. Tokens (`VISTA_BACKEND_GITHUB_TOKEN`,
  `VISTA_BACKEND_GITLAB_TOKEN`) are deployment settings, sent only to their own
  API; they make private repositories readable without entering the sandbox.
  `get_source.py` keeps only the offline cases (uploaded archives and folders).
  Self-hosted GitLab instances are not included yet; each is one more API
  prefix and host pattern.

## Risks / Trade-offs

- The validator duplicates rules. Mitigated by the parity test, which breaks
  when `_load_job` or a shipped job changes in a way the validator disagrees with.
- Replace is no more permissive than today's DELETE + import of a private skill,
  but it swaps files under a skill other projects may load. They pick it up when
  their agent next starts.
- `fetch_repository` lets the agent read any repository a configured token
  can, including when a prompt injection asks it to; the result lands only in
  that user's own workspace, but a deployment token may reach repositories the
  user cannot. Tokens are optional and should be read-only and scoped. Per-user
  tokens in settings would close the gap and are left for later.
- A wrapped private repository must be cloneable on the cluster (an SSH key
  or token there); the skill cannot carry credentials, and SKILL.md tells the
  agent never to copy them.
- Evicting the project's agents drops idle sandboxes for every member of the
  project; chat history lives in the database, so the next message rebuilds.
