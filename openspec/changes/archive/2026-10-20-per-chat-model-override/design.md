## Context

Every model call resolves through `agents/inference.py`: `resolve_inference_target(user)`
picks provider, endpoint, key and model from the user row, and `build_model_for(user)` turns
that into a pydantic-ai `Model`. `require_inference_credential(user)` refuses a turn up
front when there is no key or no model. The model is the user's `inference_model`, a
`provider:name` string the picker writes.

Callers, and what they know about a chat:

| Caller | Where | Knows the chat? |
|---|---|---|
| Chat turn, its PALISADE Q-LLMs, campaign tools | `ProjectAgent._build_agent` (`agents/agents.py:521`), `_campaign_driver_deps` (`:639`) | Yes: the pool key is `(chat_session_id, project_id, user_id)` |
| Campaign monitor, rebuilding a planner in the background | `agents/campaign/wiring.py:129` | Only via `campaign_run.session_id` |
| Report draft | `agents/report_authoring.py:77` via `services/reports.generate_draft` | No: the request carries only `message_history` |
| Skill draft | `agents/skill_authoring.py:72` via `services/skills.generate_draft` | No, same |
| Debate roles | `agents/forum/wiring.build_orchestrator` (`:500`) | Not a chat; one `model` for all roles |
| Citation extraction | `citation_credentials` (`agents/inference.py:416`) | Not chat-born; unchanged |

Facts that shape the approach:

- **Agents are pooled per chat** (`services/project_agent.py`) and evicted by
  `invalidate_agents` after a commit. Saving user settings already evicts all the user's
  agents, so chats without their own model already pick up a changed default on their next
  turn.
- **Eviction waits for in-use holders** (`utils/ttl_pool.py:142`), so a running turn
  finishes on the model it started with.
- **A debate builds its orchestrator twice**: once in `open_debate` to open the thread, then
  again in `run_debate_task` / `continue_debate_task`, in the background from the stored
  run. Per-role models therefore have to be persisted, not passed through.
- **`RoleAgents(models={role: …})` already exists** (`agents/forum/roles.py:501`); only
  tests use it.
- **New nullable columns need no migration**: `init_db` adds missing columns
  (`db/db.py:87`).
- **The picker already handles the hard UI parts**: typing an unlisted name, marking a model
  as not listed, "Default (<model>)" (from `settings-modal-revamp`).

## Goals / Non-Goals

**Goals:** a model per chat and per debate role, resolved in one place, persisted wherever
background work rebuilds its agents, and visible in the picker and the debate view.

**Non-Goals:** see the proposal. In particular, no provider per chat, no per-role defaults,
and no change to how citation extraction or PALISADE pick a model.

## Decisions

### 1. One resolver with an explicit model override

`resolve_inference_target(user, model=None)`, `build_model_for(user, model=None)` and
`require_inference_credential(user, model=None)` take an optional `provider:name`. When it
is given it replaces the row's model and the provider default. Provider, endpoint and key
are unchanged, and `model_is_default` is false. Every caller below passes the override; none
builds a `Model` by hand.

*Alternative:* a synthetic user copy with `inference_model` swapped. That's rejected
because it hides the override in a fake row that could be saved or logged as the user's
own.

### 2. Storage: one nullable model column wherever a run is rebuilt

| Column | Meaning of NULL |
|---|---|
| `chat_session.inference_model` | follow the default |
| `debate_participant.model` | this role follows the default |
| `debate_post.model` | not recorded (human posts, older posts) |
| `campaign_run.model` | older run: use the user's default, as today |

`chat_session` and `debate_participant` hold a choice. `debate_post` and `campaign_run`
hold the resolved model, the one actually used, so they read correctly after the default
moves.

### 3. Chat override: loaded at agent build, evicted on change

`_build_project_agent` already loads the user row; it also loads the chat session row when
`session_id` is set and hands `ProjectAgent` its `model_override`. `_build_agent`,
`_campaign_driver_deps` and the `require_inference_credential` call in `stream()` use it.

`ChatSessionUpdate` gains `inference_model`. Whether the field is present
(`model_fields_set`) distinguishes "clear" (explicit null) from "untouched". The update
evicts only that chat's agent: `invalidate_agents` gains a `chat_session_id` filter on
`key[0]`. `ChatSessionPublic` exposes the field so the UI can show it. `POST
/agent` also passes the chat's override to `require_inference_credential` (`api/agent.py:85`),
so a chat with its own model can send on a provider with no default.

### 4. Chat-born work

- **Reports and skill drafts:** the draft request bodies gain an optional
  `chat_session_id`. The service looks up that chat's model (checking it belongs to the
  user and project) and passes it to `generate_report_draft` / `generate_skill_draft`. The
  UI sends the open chat's id; a skill drafted from a saved report sends the
  `chat_session_id` in the report's header.
- **Campaigns:** `create_campaign_run` records the launching agent's resolved model id on
  `campaign_run.model`. The monitor's planner rebuild passes `model=run.model`. This makes
  "captured at launch" hold across the background boundary, which today silently falls
  back to the user's current default.

### 5. Debates: per-role choice on the roster, resolved per run

- `DebateCreate` and `ContinueDebate` gain `models: dict[DebateRole, str | None]`. A
  missing key or null means the default.
- `_add_roster` writes the choice to `debate_participant.model`. Continue updates the rows
  it is given and leaves the rest. Its form pre-fills from the stored choices, so leaving it
  untouched keeps them.
- `run_debate_task` and `continue_debate_task` read the roster (`models_for(session, run)`)
  and call `build_orchestrator(…, models=…)`, which builds
  `RoleAgents(model=build_model_for(opener), models={role: build_model_for(opener, model=m)})`.
  The `open_debate` orchestrator only opens the thread, so its models do not matter. It
  still receives them, so `_add_roster` can record them.
- After each role post, the orchestrator records that role's resolved model name on the
  post's `debate_post.model` row, next to `record_post_tools`.
- **The model stays local**: it is not written into the forum git post format. That matches
  the existing "records the VISTA account that wrote them locally" precedent, and keeps the
  format, which other installs read, unchanged. *Alternative:* a post trailer, so other
  installs see it too. Deferred: a format change for something nobody else reads yet.
- `DebateParticipantPublic` and `DebatePostPublic` expose `model`.

### 6. UI

- **`ModelPicker` gets a controlled core.** The menu (list, search, type-a-name, not-listed
  marking, "Default (<model>)") moves into a component that takes `value` (a model or
  null, meaning default), `onChange`, and the default's label. Three thin wrappers:
  - header, no chat open: value is the user's model, `onChange` writes `inference_model`,
    as today;
  - header, chat open: value is the chat's `inference_model`, `onChange` patches the chat
    session, the label adds "· this chat", and the menu's first item is "Use default
    (<model>)" (shown only while overridden);
  - Lab role select: value is the role's choice. Three of them sit beside Rounds on the
    open form and on the Continue control.
- `ui/app/page.tsx` passes the open chat (id and model) to the header picker. The
  conversation list passes none.
- The send-hold for "no model" (`requestModelChoice`) checks the chat's model first.
- Report and skill draft calls include the open chat's id.
- The debate view shows "Proposer <m> · Reviewer <m> · Referee <m>" and the model on each
  role post.

## Risks / Trade-offs

- **A chat's model can outlive the provider that served it.** By decision it is not
  cleared on a provider switch. The picker marks it as not listed and the turn's error
  names it. `settings-modal-revamp` clears the *default* on a provider change. These
  overrides don't get the same treatment, which is deliberate but will surprise someone.
- **Default changes move unpinned chats.** That's what was decided, and it is free here
  because user saves already evict every agent. It costs one uncached turn on providers
  with automatic prefix caching.
- **A spec archive-order dependency.** The model-picker delta modifies the same
  requirement as `settings-modal-revamp`'s delta and is written on top of it. Archive
  that change first, or this one's text will overwrite it with the same intent anyway.
- **Campaign rows from before this change** have a null `model` and keep today's behaviour
  (the user's current default) when the monitor rebuilds them.
