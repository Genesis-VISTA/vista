# Skill onboarding

This doc covers everything around adding skills to VISTA: the on-disk layout
and frontmatter schema, the three ways to get a new skill into your project
(write by hand, generate from a chat, or import from a GitHub or GitLab
repository), and how publishing to and importing from a collaboration server
works.

If you just want to use an existing skill, see the skills on this computer and
the project's loaded skills at `/skills`, and the skills published to your
collaboration server on the Skill Hub at `/skill-hub`.

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
| `author`        | string      | no       | Display name of the skill author. Kept in SKILL.md and in a published version's manifest.   |

Where a skill came from and whether it is published are not frontmatter: VISTA
records them beside the skill (`repo_url` for a repository import, and the
collaboration server, item and version for a published or imported one).

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
---

# Salt Analysis Skill
...
```

## Authoring a skill — four paths

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
---

## Overview
...
EOF
```

To attach the skill to a project so the agent actually sees it, add the slug
to the project's `skills` list (Projects → edit → Skills).

### 2. Generate from a chat conversation

When you've done a multi-step task in chat and want to capture it as a
reusable skill:

1. Have the conversation in the chat panel.
2. Click **Save as skill…** in the chat header (disabled until the chat has at
   least one turn).
3. The modal shows "Drafting from your conversation…" while the LLM runs;
   when the draft arrives, it pre-fills `name`, `description`, and a Markdown
   body that distills the workflow.
4. Edit any field, then click **Save**. To share it, publish it from `/skills`
   (see [Publishing and importing](#publishing-and-importing-through-a-collaboration-server)).

Under the hood this calls `POST /skills/generate` (which does **not** write
anything) followed by `POST /skills` with the active project's name, so the
new skill is loaded into that project.

### 3. Import from a repository

For skills hosted in a Git repository on github.com or any GitLab (gitlab.com
or self-hosted, e.g. `gitlab.osti.gov`), holding one skill or many:

1. Go to `/skills` (the active project's skill tab — **not** the hub).
2. Click **Import…** and stay on the **Repository** tab.
3. Paste a repository or a folder in one:
   - `https://github.com/<owner>/<repo>`
   - `https://github.com/<owner>/<repo>/tree/<ref>/<path>`
   - `https://<gitlab-host>/<group>[/<subgroup>…]/<repo>`
   - `https://<gitlab-host>/<group>/<repo>/-/tree/<ref>/<path>`

   A link to a `SKILL.md` itself (`/blob/…/SKILL.md`) means its folder.
4. For a private repository, paste an access token (read access to the
   repository is enough). It is sent only to the URL's host, for this import,
   and is not saved. Leave it empty to use your GitHub or gitlab.com token from
   **Settings → Repositories**; no Settings token is sent to any other host.
5. Click **Import**.

VISTA downloads the repository's archive over the host's API (no `git` binary),
pinned to the commit its branch or tag points at, and finds the skills in it:

- A `SKILL.md` at the linked folder (or the repository root) makes that folder
  the one skill — a single-skill repository imports exactly as it always did.
- Otherwise, if the repository has a `.claude-plugin/marketplace.json`, the
  skill folders it lists (an entry that is a folder of skills means every skill
  in it; a plugin hosted in another repository is left out).
- Otherwise every folder below with a `SKILL.md`, at any depth, skipping
  `.git`, `node_modules` and other dot-folders. A folder holding a `SKILL.md` is
  one skill, including everything below it.

One skill with no conflict is imported straight away. Otherwise the dialog lists
every skill found — name, description, folder, file count and HPC jobs — to tick
the ones to import (or **Select all**). Each skill's folder is copied whole.
Beyond it, the dialog also shows:

- **Also copied in** — the nearest `LICENSE`, `NOTICE`, `ATTRIBUTION` or
  `COPYING` from the folders above it, when the skill has none of its own, and
  any file a symlink in the skill points at elsewhere in the repository (copied
  in as the file).
- **⚠ warnings** — a symlink that leaves the repository (left out), or a
  `../` path in the skill's Markdown that leads out of its folder. That file is
  not imported; the skill may need it.
- An error for a skill whose `SKILL.md` is malformed; it cannot be ticked, and
  the rest still import.

If a skill's name is already taken, choose **Skip**, **Replace my skill** (not
offered for one bundled with VISTA), or **Import under another name**,
which rewrites only the `name:` line of its frontmatter. Skills in one
repository that share a name are ticked only once; give the others a new name
to import them too. Each skill is imported
on its own and the dialog says what happened to each. An imported skill is
loaded into the project, and its `repo_url` is its folder at the
imported commit (or, for a single URL, the URL given). The upload limits apply
to each skill: 2,000 files and 50 MB.

### 4. Have the agent write it (HPC skills)

"Save as skill" drafts a SKILL.md body only. For a skill that ships its own cluster job, ask the
agent to write the whole folder:

1. Load the bundled **hpc-skill-author** skill into the project (Projects → edit → Skills).
2. In chat, ask for it: *"make an HPC skill that runs X on Lux"*. The skill gives the agent the
   job contract, templates for a skill and a Lux job, and a validator. The agent writes the new
   skill to `/mnt/data/output/<skill>/` and runs
   `python3 /mnt/skills/hpc-skill-author/scripts/validate.py /mnt/data/output/<skill>` until
   it prints `OK`.
3. Open `/skills` → **Import…** → **From project output**, pick the folder, and click
   **Import**. The list shows each folder's skill name and the HPC jobs it ships.

**From existing code.** The agent can also wrap code you already have instead of writing a job
from scratch: *"make an HPC skill from https://github.com/owner/repo"* (or a `gitlab.com` URL,
subgroups included), or upload a `.zip` of a
folder (or of a private repository) in chat and ask it to wrap that. For a URL, the agent calls the
`fetch_repository` tool: VISTA's backend, not the sandbox, downloads the repository, pinned to the
commit its branch or tag points at now, into the agent's `/mnt/data/work/<name>/`. That is the
same route as **Import → Repository**, so it works on networks whose HTTPS proxy the sandbox does
not trust, and with a GitHub or gitlab.com token saved in **Settings → Repositories** it reads
private repositories on that host without the token entering the sandbox (the agent can read
whatever the token can, so scope it read-only). An upload is unpacked in the sandbox by
`scripts/get_source.py`. Either way the skill's `inventory.py` lists READMEs, build files, existing batch
scripts and their `#SBATCH` lines, entry points and their flags. The agent proposes what the job
runs and which flags become `script_args`, and translates an existing batch script's resources
into `cluster_defaults.json`. A repository is not copied into the skill: its job, from the
`wrap-repo` template, clones the repository on the cluster at the pinned commit
(`$VISTA_JOB_DIR/src/<commit>`, once, from `setup_lux.sh` on the Lux login node). An uploaded
folder's needed files are shipped in the job folder. Only github.com and gitlab.com are fetched (for another host, upload an archive). A private
repository's needed files are shipped in the skill unless you confirm the cluster can clone it
with your own credentials there. A skill that already ships `hpc_jobs/` in VISTA's format needs no wrapping:
import it directly from its repository or a local folder.

**Software environments.** The skill tells the agent to prefer an environment you have already
built on the cluster (its path goes in `cluster_defaults.json`), and to recommend pre-building one
when it is large. When the job must build a conda environment itself, the skill's
`templates/conda-env/build_conda_env.sh` builds it once into `$VISTA_JOB_DIR` and reuses it: it exports
the OLCF proxy first, keeps conda's package cache and pip's build directory under `/tmp/$USER`
rather than on Lustre (`CONDA_PKGS_DIRS`, `TMPDIR`), and sets the env's folder to a stripe count
of 1. The validator warns about a job that creates a conda env without moving the package cache.
See [OLCF's documentation](https://docs.olcf.ornl.gov/) for each cluster's recommended modules.

The import applies the same checks and limits as a local-folder import and refuses a folder
holding a symlink (the sandbox can create links to files outside it). The skill
is added to the project's `skills` list, and shows the **Runs HPC jobs** badge; from the next
message on, the agent can submit its jobs as `<skill>/<job>`. To change it, ask the agent to edit
the folder and import it again: a folder whose name matches one of your skills shows
**Replace**, which swaps that skill's files, keeps it in the project and marks it changed since
it was last published. The name of a skill bundled with VISTA cannot be reused.

The agent cannot import or load a skill itself; loading a skill lets it run the skill's scripts
on a cluster under your credentials, so that step stays a click. The routes behind the tab are
`GET` and `POST /projects/{project}/skills/from-output`; they read only the caller's own output
folder in that project.

## Publishing and importing through a collaboration server

Skills are shared through a collaboration server, explicitly: nothing leaves
your computer until you publish it, and nothing arrives until you import it.
Set the server's address in **Settings → Collaboration**; without one that
answers, the Skill Hub and every Publish button say why and do nothing. (For
development, `./launch.sh logs --collab` starts a local server.)

**Publishing.** On `/skills`, a skill's card says where it stands — *Not
published*, *Version 3*, *Changed here since version 3*, *Version 4 available* —
and **Publish** (or **Publish changes**) sends its folder, as an archive, with a
manifest read from its SKILL.md. The first publish asks you to confirm, since
anyone who can reach the server can see and import it; there is no unpublish,
but you can publish newer versions. Skills bundled with VISTA are never
published: every installation has them already.

The server numbers versions 1, 2, 3… and accepts a publish only on top of its
newest one. If someone published since your copy's version, the publish is
refused and you choose: **Use the server's version** (import it over your
changes), **Publish mine as version N** (on top of theirs), or **Publish as a
new skill**. Any edit — a `PATCH`, a **Replace** from project output or a
repository — marks a published skill changed since its version.

**Importing.** The Skill Hub at `/skill-hub` lists the server's skills with what
their newest version says about them: description, who published it, and the
HPC jobs it ships. Importing is your decision to trust a skill; review it first.
**Import** unpacks it, with the same checks and limits as a local folder, and
loads it into the active project. A skill you already have from the same item
shows **Update to version N** instead, which keeps its name and asks before it
replaces changes made here. A different skill with the same name asks for a new
name to import it under.

Deleting a skill on `/skills` removes it from this computer only; its published
versions stay on the server. Skills bundled with VISTA cannot be deleted, only
unloaded. A new import is named after the skill on the server; a copy that was
changed here but is at the newest version offers **Revert to version N**.

The routes are `POST /skills/{name}/publish` (`{on_top_of?, as_new?}`),
`GET /collaboration/skills` and `POST /collaboration/skills/{id}/import`
(`{version?, rename_to?, replace_modified?, project?}`); a refusal that needs
your choice is a 409 with a `code` (`version_conflict`, `remote_missing`,
`modified`, `name_taken`).

## How a skill becomes "loaded" for a project

A project's loaded skills are its `skills` list in the project record — the
agent copies exactly those into its sandbox and lists them in its system
prompt. There is nothing else: what `/skills` shows as loaded is what the
agent has.

The list changes in four places, all for every member of the project:

- **Load** and **Unload** on `/skills`, and **Load / Loaded ✓** on the hub,
  rewrite the project with `PUT /projects/<name>`.
- **Import** and **Save** (from a chat), and **Import** on the hub, pass the
  active project to `POST /skills/import`, `POST /skills/import/upload`,
  `POST /skills` or `POST /collaboration/skills/{id}/import`, which
  load the new skill into it in the same commit. **Import → From project
  output** does the same through `POST /projects/<name>/skills/from-output`.
- The project modal's skills picker.

Every change rebuilds the project's agents, so the next turn sees it. A
**Replace** from project output rebuilds them too, since the skill's files
changed even though the list did not.

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
`submit_hpc_job` only runs code from an `hpc_jobs/<name>/` directory (staged and
launched by [`submit_job_mcp.py`](../mcp_servers/vista_mcp_server/src/vista_mcp_server/submit_job_mcp.py)),
either VISTA's own or one inside the skill (see
[HPC jobs inside a skill](#hpc-jobs-inside-a-skill)).
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

A campaign binds a sim skill to a role in its `campaign.yaml`, where two optional fields
make that contract explicit: `args` (how a candidate is encoded into `script_args` —
use `encoding: flags` with a `map` of variable → CLI flag, since job wrappers parse flat
flags with argparse) and `collect_files` (the output files the result parser needs, e.g.
`[results.json]`). Both are opt-in: a role declaring neither keeps the older behavior of
a JSON-serialized candidate and no fetched outputs.

## HPC jobs inside a skill

A skill can carry its jobs itself, at `<skill>/hpc_jobs/<job>/`, so adding an HPC skill needs no
change to VISTA. A project that loads the skill can submit them as `<skill>/<job>`. The layout,
the job contract and what to watch for (nested files, `setup_lux.sh`, a `KEY=VALUE` allow-list
for `script_args`) are in
[hpc-jobs.md](hpc-jobs.md#self-contained-hpc-skills). The SKILL.md should say which jobs the skill
ships, which arguments they take and how to read their outputs, since the `submit_hpc_job` tool
description lists only VISTA's own jobs.

**Writing one.** The bundled `hpc-skill-author` skill has the agent write such a skill for you,
check it with `scripts/validate.py`, and leave it in the project output for one-click import
(see [Have the agent write it](#4-have-the-agent-write-it-hpc-skills)). The validator applies
everything `submit_hpc_job` checks (job script, `# <job>` README heading, a parsing
`cluster_defaults.json` with a section per job script, Lux partition and GPUs) and everything
the import checks (SKILL.md, no symlinks, size limits), and requires the skill's folder to match
its `name` and its SKILL.md to name each job as `<skill>/<job>`. It also warns about what VISTA
cannot check: a job that never uses `$VISTA_OUT`, takes `script_args` without a `KEY=VALUE`
allow-list, or writes into `$RUN_DIR_<Cluster>`. It runs on any skill folder, so use it on a
hand-written one too. `test_skill_author_validator.py` in the MCP server's tests keeps it in step
with the server.

**Trust.** A skill job runs the skill's scripts on a cluster as the researcher: under their
S3M, NERSC or Globus credentials, or their own SSH login on Lux. VISTA's own `hpc_jobs/` is
reviewed with the code; a skill imported from GitHub is not. So:

- The `/skills` page marks every skill that ships jobs with a **Runs HPC jobs** badge naming
  them. The list comes from the skill's folder on disk, not from anything SKILL.md declares, so
  a skill cannot hide it. Read an imported skill's `hpc_jobs/` before loading it.
- A skill's jobs are available only in projects that load the skill, and only through the agent
  calling `submit_hpc_job`. The researcher must have connected that cluster, and Lux also asks
  for a login once per session.
- Not done yet: a per-project "allow this skill to run cluster jobs" approval, which would leave
  the skill's `hpc_jobs/` out of the project's skills volume until a researcher allows it.

## Troubleshooting

**"Invalid skill name 'X'; must be kebab-case…"** — Slugs must be lowercase
letters/digits separated by single dashes, e.g. `salt-analysis`. No
underscores, no capitals.

**`409 — A skill named 'X' already exists`** — Pick a different name (the
slug is the directory name and must be unique).

**`… has been published since version N`** — Someone published the skill
after your copy's version; choose what to do in the dialog (see
[Publishing and importing](#publishing-and-importing-through-a-collaboration-server)).

**`… is not GitHub, and did not answer as a GitLab server`** — Import reads
github.com and GitLab hosts only. Check the host; for anything else, download
the folder and use **Local folder**.

**`<host> has no public repository '…' (404)`** — Either the repository really
doesn't exist, or it's private and no token was sent. Paste a token in the
dialog, or save a GitHub or gitlab.com token in Settings → Repositories.

**`<host> refused access (401/403)`** — The token was sent but is wrong,
expired, or cannot read the repository. The message says whether it was the
dialog's token or the Settings one.

**`Could not reach <host>`** — The host is down, or not reachable from where
the backend runs (VPN, proxy).

**`No SKILL.md found in …`** — Neither the linked folder nor anything below it
holds a `SKILL.md`. Check the branch and path in the URL.

**`… holds N skills (…)`** — `POST /skills/import` takes one skill. In the
dialog you choose among them; over the API, use `POST /skills/import/scan` and
`POST /skills/import/batch`, or link one skill's folder.

**`SKILL.md in <folder> is invalid: …`** — Its frontmatter is missing, is not
valid YAML, or lacks a valid `name` or `description`; the message names the field.

**Skill appears in `skills/` but not on `/skills`** — The skill's `name:`
frontmatter must match the directory name. Backend logs `read_skill` parse
errors on startup; check there.

**My skill doesn't show on the hub** — The hub lists the collaboration server's
skills, not this computer's. Publish it from `/skills`; skills bundled with
VISTA are never published.

## Reference

- Schema: [backend/src/vista_backend/agents/skills.py](../backend/src/vista_backend/agents/skills.py) (`SkillMetadata`)
- Authoring agent: [backend/src/vista_backend/agents/skill_authoring.py](../backend/src/vista_backend/agents/skill_authoring.py)
- Import logic: [backend/src/vista_backend/agents/skill_import.py](../backend/src/vista_backend/agents/skill_import.py)
- API routes: [backend/src/vista_backend/api/skills.py](../backend/src/vista_backend/api/skills.py)
- Frontend loaded-skills helpers: [ui/lib/loaded-skills.ts](../ui/lib/loaded-skills.ts)
