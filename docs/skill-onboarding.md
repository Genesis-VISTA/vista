# Skill onboarding

This doc covers everything around adding skills to VISTA: the on-disk layout
and frontmatter schema, the three ways to get a new skill into your project
(write by hand, generate from a chat, or import from GitHub), and how
publishing to the Skill Hub works.

If you just want to consume an existing skill, see the Skill Hub at `/skill-hub`
and the project's loaded skills at `/skills`.

## Where skills live

```
skills/
└── <name>/
    ├── SKILL.md     # required — AgentSkills-style YAML frontmatter + Markdown body
    ├── scripts/     # optional — files referenced from the body
    └── ...          # any other assets the body refers to
```

`<name>` is the slug — kebab-case lowercase, e.g. `salt-analysis`. It must
match the `name:` field in the frontmatter and is used as the URL slug in API
routes and on the hub card.

The skills directory is configured by the `VISTA_SKILLS_DIR` env var (defaults
to `../skills` relative to the backend cwd).

## SKILL.md frontmatter

| Field           | Type        | Required | Notes                                                                                       |
| --------------- | ----------- | -------- | ------------------------------------------------------------------------------------------- |
| `name`          | string      | yes      | Kebab-case slug; must match the directory name.                                             |
| `description`   | string      | yes      | One to three sentences. Lead with a verb. The agent sees this when deciding to use the skill. |
| `license`       | string      | no       | Free-form license note.                                                                     |
| `compatibility` | string      | no       | Free-form compatibility note.                                                               |
| `allowed_tools` | string      | no       | Reserved by the AgentSkills spec; not enforced today.                                       |
| `metadata`      | mapping     | no       | Open key-value bag. By convention we use `metadata.version` and `metadata.tags`.            |
| `tags`          | string list | no       | Top-level tag list (used by the hub's tag filter).                                          |
| `author`        | string      | no       | Display name of the skill author. Rendered as "by ..." on cards.                            |
| `repo_url`      | string      | no       | External URL for the skill's source. Rendered as the "repo ↗" link on cards.                |
| `is_public`     | bool        | no       | Defaults to `false`. `true` lists the skill on the Skill Hub. **Publishing is one-way** — see below. |

Example:

```yaml
---
name: salt-analysis
description: Analyze molten salt thermophysical properties from the MSTDB-TP database.
metadata:
  version: "0.3.0"
  tags: ["Materials Design", "Molten Salt Tritium Breeding"]
license: Proprietary
author: VISTA Team
repo_url: https://code.ornl.gov/v28/vista
is_public: true
---

# Salt Analysis Skill
...
```

## Authoring a skill — three paths

### 1. Hand-write a SKILL.md

For total control, just create the directory and file yourself. The backend
loads skills off disk on every request, so a fresh skill becomes available the
next time the agent runs.

```bash
mkdir skills/my-new-skill
cat > skills/my-new-skill/SKILL.md <<'EOF'
---
name: my-new-skill
description: Describe what it does and when an agent should invoke it.
author: Your Name
is_public: false
---

## Overview
...
EOF
```

To attach the skill to a project so the agent actually sees it, add the slug
to the project's `skills` list. (Project skill editing from the UI is on the
roadmap — until then, edit the project's row in `vista.db` or `defaults.py`.)

### 2. Generate from a chat conversation

When you've done a multi-step task in chat and want to capture it as a
reusable skill:

1. Have the conversation in the chat panel.
2. Click **Save as skill…** in the chat header (disabled until the chat has at
   least one turn).
3. The modal shows "Drafting from your conversation…" while the LLM runs;
   when the draft arrives, it pre-fills `name`, `description`, and a Markdown
   body that distills the workflow.
4. Edit any field, then click **Save (private)** or **Save & publish**.

Under the hood this calls `POST /skills/generate` (which does **not** write
anything) followed by `POST /skills`. The generated skill is auto-loaded into
the active project's loaded-skills set so it appears on `/skills` immediately.

### 3. Import from GitHub

For skills hosted in a Git repository (yours or someone else's):

1. Go to `/skills` (the active project's skill tab — **not** the hub).
2. Click **Import…**.
3. Paste either:
   - `https://github.com/<owner>/<repo>` — SKILL.md at the repo root, or
   - `https://github.com/<owner>/<repo>/tree/<ref>/<subpath>` — SKILL.md inside
     a sub-directory of a monorepo. Example:
     `https://github.com/anthropics/skills/tree/main/skills/docx`.
4. Click **Import**.

The backend downloads the repo tarball via the GitHub API (no `git` binary
required), validates that SKILL.md exists at the target path, copies the
**entire** skill directory (SKILL.md + scripts + any other files) into
`skills/<name>/`, forces `is_public:false`, and auto-fills `repo_url` from the
source URL if the imported frontmatter didn't already set it.

For private repos, set `VISTA_BACKEND_GITHUB_TOKEN` in `.env` to a GitHub
personal access token. Without a token, public repos work but the API rate
limit is 60 requests/hour per IP. Public repos look like 404s when you're
unauthenticated and the repo is actually private.

## Publishing to the Skill Hub

Every skill starts out **private** (`is_public:false`). It's visible on
`/skills` for the project that owns it but hidden from the hub. To publish:

1. Open `/skills`.
2. On the skill's card, click **Publish**.
3. Confirm the permanence dialog.

The card flips to **Published ✓** and the skill now appears on `/skill-hub`
for anyone with access.

**Publishing is one-way.** The backend rejects `PATCH /skills/{name}` with
`is_public: false` if the skill is currently public, returning HTTP 409. There
is no Unpublish UI. If you really need to take a skill down, delete the
`skills/<name>/` directory by hand.

## How a skill becomes "loaded" for a project

A project's `skills` list (in the project record) defines the **mandated**
skills — these are always loaded and the agent always sees their SKILL.md in
the system prompt.

On top of that, users can **add** skills to a project via the **Load** button
on the hub or by generating/importing new ones. These additions live in
browser localStorage under `vista.loadedSkills.v2`, keyed by project name, so
switching projects swaps the additions in and out.

The `/skills` page shows `loadedSlugs = project.skills ∪ additions[project]`.
Project-mandated skills carry a `required` chip and cannot be unloaded from
the UI; user additions show a normal `Unload` button.

## Lightweight & HPC-backed skills (clone at runtime)

A skill does **not** have to vendor its implementation into VISTA. When the
tooling already lives in a public Git repo — especially a heavy simulation
package — the skill can ship just a `SKILL.md` and clone the code **at runtime**.
This keeps the VISTA tree small and lets the skill track its upstream repo.

`salt-chemistry-md` is the reference example (OpenMM + MACE molten-salt MD). It
runs across **two execution contexts**, each of which bootstraps the code itself:

| Context | Where | Runs | How the code arrives |
| --- | --- | --- | --- |
| **Sandbox (CPU)** | `run_bash` sandbox | structure build, analysis, plotting | the agent `curl`s + `tar`s the repo tarball into `/mnt/data/output` (the sandbox has egress; `git` isn't installed, so use the tarball URL) |
| **HPC (GPU)** | OLCF Frontier | the GPU simulation | a thin `hpc_jobs/<job>/` clones the repo on the compute node |

The GPU path is the only part that **can't** be pure clone-at-runtime: VISTA's
`submit_hpc_job` only runs code from an `hpc_jobs/<name>/` directory (Globus-staged and
launched by [`submit_job_mcp.py`](../mcp_servers/vista_mcp_server/src/vista_mcp_server/submit_job_mcp.py)).
So an HPC-backed skill pairs a `SKILL.md`
with a **thin bootstrap job** — no simulation code vendored, just:

- `README.md` (starts with `# <job>`), `cluster_defaults.json` (per-cluster resources +
  `iri.environment` for things like the repo URL/ref and a pre-provisioned env path),
- `job.<cluster>.slurm` that **clones the public repo** on the node (the compute node's
  HTTP proxy is pre-exported by the dispatcher), **activates a pre-provisioned conda env**
  (build it once on the cluster — a fresh `conda env create` per job would blow the
  walltime), then runs the simulation, writing outputs to `$VISTA_OUT`,
- optionally a small wrapper script (the *only* non-metadata file, so it's the one thing
  staged to `$RUN_DIR_<Cluster>`) that turns one flat `script_args` order into the repo's
  CLI calls, and a `setup_<cluster>.sh` pre-launch validation gate.

See [`hpc_jobs/salt-chemistry-md/`](../hpc_jobs/salt-chemistry-md) for the worked job and
[`db/skills/salt-chemistry-md/SKILL.md`](../backend/src/vista_backend/db/skills/salt-chemistry-md/SKILL.md)
for the skill, including the **sim-skill dispatch/collect contract** (`order → script_args
→ submit_hpc_job`; `results.json → structured result`) that lets a campaign planner fan
out one subagent per state point — see the
[multi-agent framework](./multi-agent-framework.md).

## Troubleshooting

**"Invalid skill name 'X'; must be kebab-case…"** — Slugs must be lowercase
letters/digits separated by single dashes, e.g. `salt-analysis`. No
underscores, no capitals.

**`409 — A skill named 'X' already exists`** — Pick a different name (the
slug is the directory name and must be unique).

**`409 — Published skills cannot be unpublished`** — Publishing is one-way;
delete the directory by hand if you need to remove a skill.

**`400 — URL '…' is not a GitHub repo URL`** — The import endpoint only
accepts `github.com` URLs in one of the two forms described above.

**`GitHub returned 404 for …`** — Either the repo really doesn't exist, or
it's private and you haven't set `VISTA_BACKEND_GITHUB_TOKEN`.

**`No SKILL.md found at <subpath>`** — Double-check the tree URL points to a
directory containing a SKILL.md (or skill.md). If you're pointing at a
monorepo root that doesn't have a SKILL.md, use the `/tree/<ref>/<subpath>`
form instead.

**Skill appears in `skills/` but not on `/skills`** — The skill's `name:`
frontmatter must match the directory name. Backend logs `read_skill` parse
errors on startup; check there.

**My private skill (alloy-design, etc.) doesn't show on the hub** — That's by
design — the hub only lists `is_public:true` skills. Private project skills
live on `/skills` instead.

## Reference

- Schema: [backend/src/vista_backend/agents/skills.py](../backend/src/vista_backend/agents/skills.py) (`SkillMetadata`)
- Authoring agent: [backend/src/vista_backend/agents/skill_authoring.py](../backend/src/vista_backend/agents/skill_authoring.py)
- Import logic: [backend/src/vista_backend/agents/skill_import.py](../backend/src/vista_backend/agents/skill_import.py)
- API routes: [backend/src/vista_backend/api/skills.py](../backend/src/vista_backend/api/skills.py)
- Frontend loaded-skills helpers: [ui/lib/loaded-skills.ts](../ui/lib/loaded-skills.ts)
