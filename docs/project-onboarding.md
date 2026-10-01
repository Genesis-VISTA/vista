# Project onboarding

A **project** is a saved bundle of agent context (system prompt, skills,
tools, usage limits) that scopes a chat session. Picking a project decides
what the agent knows about, what skills it has access to, what MCP tools it
can call, and how aggressively it can spend on a turn.

This doc covers the project data model, the default projects, the
CRUD UI at `/projects`, and how a project's record actually feeds the agent
loop at runtime.

If you just want to start chatting, pick a project on `/projects` and click
**Open**; the active project's name is remembered in your browser.

## Where projects live

Projects are rows in the backend SQLite DB (default: `vista.db` at the repo
root; override with `VISTA_BACKEND_DATABASE_URL`). The table is created and
seeded on backend startup by `init_db` in
[db.py](../backend/src/vista_backend/db/db.py), which calls into
[seed.py](../backend/src/vista_backend/db/seed.py). Seeding is **additive**:
it inserts what is missing, matched by a fixed id, and never overwrites a row
that exists, so edits you make to a project survive a restart and user-created
projects are never touched. See [Default projects](#default-projects).

The system prompt for each default project is loaded from a sibling
Markdown file under [system_prompts/](../backend/src/vista_backend/db/system_prompts).

## Project schema

| Field           | Type                          | Required | Notes                                                                                |
| --------------- | ----------------------------- | -------- | ------------------------------------------------------------------------------------ |
| `name`          | string, 2–80 chars            | yes      | Unique. Pattern `[A-Za-z0-9_ .-]+`. Used as the URL slug on every project API route. |
| `description`   | string                        | no       | One-liner shown on the project card.                                                 |
| `system_prompt` | string                        | no       | Appended after the base prompt (see "How a project drives the agent" below).         |
| `skills`        | list of skill slugs           | no       | The project's **mandated** skills — always loaded, always in the system prompt.      |
| `tools`         | list of fnmatch patterns      | no       | MCP tool allow/deny list. `!`-prefixed entries deny; no allow means `*`.             |
| `usage_limits`  | mapping (PydanticAI shape)    | no       | Per-turn limits enforced by the agent loop. Empty = engine defaults.                 |

The schema lives in
[backend/src/vista_backend/db/schemas.py](../backend/src/vista_backend/db/schemas.py).
The on-the-wire shape (`ProjectPublic`) adds `id` (a server-assigned UUID);
the frontend `Project` type in [ui/lib/projects.ts](../ui/lib/projects.ts)
camel-cases everything (`systemPrompt`, `usageLimits`).

### `tools` patterns

The list is consumed by `tool_allowed` in
[agents.py:95](../backend/src/vista_backend/agents/agents.py). Rules:

- Entries that don't start with `!` are **allow** patterns; entries with `!`
  are **deny** patterns. A tool name passes iff at least one allow matches and
  no deny matches.
- Patterns are `fnmatch`-style — `*` and `?` wildcards over the tool name.
- An empty list (or all-deny list) implicitly allows everything (`*`).

Every default project uses the same list, so HPC job submission is available
and only the retired `agenthpc_*` SSH tools are denied:

```python
tools = ["*", "!agenthpc_*"]
```

### `usage_limits`

Forwarded straight into PydanticAI's `UsageLimits`
([reference](https://ai.pydantic.dev/api/pydantic-ai/usage/#pydantic_ai.usage.UsageLimits)).
Common fields:

| Key                  | Meaning                                                  | Engine default |
| -------------------- | -------------------------------------------------------- | -------------- |
| `request_limit`      | Max model requests per turn (each tool call adds one).   | 50             |
| `tool_calls_limit`   | Max successful tool calls per turn.                      | unlimited      |
| `input_tokens_limit` | Max input/prompt tokens per turn.                        | unlimited      |
| `output_tokens_limit`| Max output tokens per turn.                              | unlimited      |
| `total_tokens_limit` | Combined input+output cap per turn.                      | unlimited      |

The default projects use `{"request_limit": 50}` (ai-safety-autonomous-labs),
`{"request_limit": 100}` (molten-salt) and `{"request_limit": 600}`
(alloy-design, long agentic loops on HPC). Empty `{}` falls back to engine
defaults.

The schema's `_validate_usage_limits` validator normalizes the dict through
PydanticAI's `TypeAdapter` on write, so a typo in a key surfaces as a
422 from the API rather than a silent no-op at run time.

## Default projects

Seeding lives in [seed.py](../backend/src/vista_backend/db/seed.py). There is
no `defaults.py`: the project rows are written inline there, each with a fixed
UUID so they can be recognised across restarts.

**Always seeded**

- **ai-safety-autonomous-labs** — AI Safety in Autonomous Labs. Its system
  prompt frames a small literature corpus on the safety and security of
  autonomous labs and LLM agents. It has no mandated skills, allows every tool
  except `agenthpc_*`, and has `request_limit` 50. It comes with the
  `ai-safety` knowledge base ("AI Safety Papers"), built from vista-data's
  `ai-safety/` folder.

**Science projects, off by default**

- **molten-salt** — Molten-salt thermophysical properties and the SPLASH
  campaign, with the `molten-salt-papers` knowledge base.
- **alloy-design** — High-entropy alloy design on HPC.

The science projects, the molten-salt corpus and the MSTDB assets behind the
`salt-analysis`, `salt-prediction` and `model-fine-tuning` skills are seeded
only when `VISTA_BACKEND_SEED_SCIENCE_PROJECTS=true` (from a vista-data token;
a prebuilt package instead seeds whatever its payload contains and ignores the
setting). Every other bundled skill is registered either way.

### When each default is seeded

- **First run, empty database** (`seed_db`): the science projects if enabled,
  the bundled skills, and the dev test users.
- **Every startup** (`sync_default_projects`): the AI-safety project and its
  knowledge base. This is what brings the project to a database that already
  exists. It inserts the project and the knowledge base if either is missing,
  and appends the `ai-safety` slug to the project's knowledge bases when the
  knowledge base exists and is not attached. It overwrites nothing else and
  removes nothing.

The science projects are never added to a database that already has projects,
even with the setting on.

### Where the AI-safety corpus comes from

With `VISTA_DATA_TOKEN` set, the first startup downloads `ai-safety/` from
vista-data and indexes it (about a minute, with citations when an inference key
is configured). If vista-data cannot be reached, startup still completes: the
project is seeded without the knowledge base, a warning names the missing
corpus, and the next startup tries again. With neither a token nor a bundled
payload the project exists but has no literature search.

A prebuilt package ships the corpus already indexed, and its first-run setup
never indexes; an absent or empty bundled index fails setup naming the corpus.

### Editing and deleting defaults

Editing a default project from the UI is safe: the next startup leaves your
changes alone, and a knowledge base you attach stays attached. **Deleting
`ai-safety-autonomous-labs` brings it back on the next startup**, with its
default settings, because nothing records that it was deleted. The science
projects are only created on first run, so deleting them is permanent.

## Creating and editing projects in the UI

The page at `/projects` ([app/projects/page.tsx](../ui/app/projects/page.tsx))
is the CRUD surface.

1. **New project.** Click `+ New project` on the page, or use the `+ New project`
   link in the nav rail (which routes here with `?new=1` so the create modal
   opens automatically). Required fields are `name` and `description`; the
   rest are optional.
2. **Skills picker.** Fed by `GET /api/skills` — the list includes every
   skill on disk (including private ones), so you can attach a freshly
   generated or imported skill without publishing it first. Mandated skills
   live forever in `project.skills`; users can still **add** extra skills per
   project via the hub's Load button (see the [skill onboarding doc](skill-onboarding.md#how-a-skill-becomes-loaded-for-a-project)).
3. **Tools field.** Comma-separated fnmatch patterns. Same syntax as the
   default projects' lists, e.g. `*, !agenthpc_*`.
4. **System prompt.** Free-form Markdown appended after the base prompt.
   Reference the project's skills by their slug — at runtime the agent sees
   an `<available_skills>` block listing each skill's `SKILL.md` path inside
   the sandbox at `/mnt/skills/<slug>/SKILL.md`.

The frontend validates the name client-side (length, character set, uniqueness)
before posting. Saving calls `POST /api/projects` for create and `PUT
/api/projects/<name>` for edit. **PUT is a full overwrite** — the modal echoes
the existing `usage_limits` back when saving so they aren't reset.

### Open / Activate

Clicking **Open** writes the project's `name` to localStorage under the
`vista.activeProject.v1` key (see
[lib/projects.ts:30](../ui/lib/projects.ts)) and navigates to `/`. The chat
page reads that key, and on activation it resets `messages` and
`messageHistory` so context doesn't leak across projects. The active project
also seeds the per-project loaded-skills additions set on `/skills` and the
hub.

### Renaming and deleting

Rename via the **Edit** modal — the frontend automatically repoints the
active-project pointer if you rename the project you're currently on, so it
doesn't dangle.

**Delete** prompts for confirmation. There is no soft-delete and no undo.
If you delete the active project, the pointer is cleared and you land back at
"no active project".

## How a project drives the agent

When you send a chat message, the frontend posts to
`POST /projects/<name>/agent/run` with the user prompt and the prior
message history. The backend builds a one-shot agent per turn in
[`ProjectAgent._build_agent`](../backend/src/vista_backend/agents/agents.py):

1. **System prompt.** Concatenation of three parts:
   - `base_system_prompt.md` (the global VISTA preamble),
   - the project's `system_prompt` field (if non-empty),
   - the rendered `<available_skills>` block listing every skill in
     `project.skills`, each pointing at `/mnt/skills/<slug>/SKILL.md`
     inside the sandbox.
2. **Toolset.** The MCP server's tools, filtered through `tool_allowed`
   against `project.tools`.
3. **Usage limits.** `UsageLimits(**project.usage_limits)` is enforced
   per-turn.

The agent loop is otherwise stateless — chat history is held by the frontend
and sent back on every turn — but everything above is rebuilt fresh from the
project record, so a project edit takes effect on the very next message.

## API reference

The backend routes live in
[backend/src/vista_backend/api/projects.py](../backend/src/vista_backend/api/projects.py);
the Next.js proxies in [ui/app/api/projects/](../ui/app/api/projects).

| Method   | Path                          | Notes                                                            |
| -------- | ----------------------------- | ---------------------------------------------------------------- |
| `GET`    | `/projects`                   | List. Frontend cache-busts via `lib/projects.ts:refreshProjects`. |
| `GET`    | `/projects/{name}`            | Single record. 404 if missing.                                   |
| `POST`   | `/projects`                   | Create. 409 on duplicate `name`.                                 |
| `PUT`    | `/projects/{name}`            | Full overwrite. 409 if the new name collides with another row.   |
| `DELETE` | `/projects/{name}`            | Hard delete. 204 on success.                                     |
| `POST`   | `/projects/{name}/agent/run`  | Run one agent turn (stateless; pass `message_history`).           |

## Troubleshooting

**`409 — A project named "X" already exists.`** — `name` is unique. Pick
a different one or delete the existing project first.

**`422 — Invalid usage_limits`** — A key not recognized by PydanticAI's
`UsageLimits`. Run `python -c "from pydantic_ai import UsageLimits; help(UsageLimits)"`
to see the accepted fields.

**Deleted `ai-safety-autonomous-labs`, restarted, it is back.** — It is a
default that is synced on every boot, matched by UUID. Edits to it are kept;
only a missing project is recreated. See
[Editing and deleting defaults](#editing-and-deleting-defaults).

**`ai-safety-autonomous-labs` has no literature search.** — Its `ai-safety`
knowledge base could not be obtained: look for an "AI-safety corpus
unavailable" warning in the backend log. Set `VISTA_DATA_TOKEN` (or use a
package built with its payload) and restart.

**`molten-salt` and `alloy-design` are missing.** — They are off by default.
Set `VISTA_BACKEND_SEED_SCIENCE_PROJECTS=true` and seed an empty database; they
are not added to one that already has projects.

**Agent can't see my new tool.** — The MCP server lists tools at
`http://localhost:8000/mcp`. If the tool is there but the agent ignores it,
check `project.tools` — a leading `!*` or a missing allow pattern shuts the
tool out.

**Skill I added to the project isn't in the system prompt.** — The skill's
SKILL.md must parse cleanly (`read_skill` is called on every turn). Backend
logs `Failed to parse skill <dir>` on errors. Also verify the skill's
`name:` frontmatter matches the directory name and the project's `skills`
list uses that same slug.

**Switched projects but the old conversation is still there.** — Project
changes reset `messages` and `messageHistory` on the chat page; if you don't
see the reset, you may be looking at a stale tab. The active-project pointer
is also `storage`-event synced across tabs.

## Reference

- Schema: [backend/src/vista_backend/db/schemas.py](../backend/src/vista_backend/db/schemas.py)
- Default projects and their sync: [backend/src/vista_backend/db/seed.py](../backend/src/vista_backend/db/seed.py)
- DB init: [backend/src/vista_backend/db/db.py](../backend/src/vista_backend/db/db.py)
- API routes: [backend/src/vista_backend/api/projects.py](../backend/src/vista_backend/api/projects.py)
- Agent build per project: [backend/src/vista_backend/agents/agents.py](../backend/src/vista_backend/agents/agents.py)
- Frontend data layer: [ui/lib/projects.ts](../ui/lib/projects.ts)
- Projects page: [ui/app/projects/page.tsx](../ui/app/projects/page.tsx)
- Skill loading for a project: [docs/skill-onboarding.md](skill-onboarding.md)
