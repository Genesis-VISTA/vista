## Why

A researcher can only change which LLM VISTA uses by typing a raw provider-prefixed model
string (e.g. `openai:claude-sonnet`) into a free-text field in the settings modal, with no way
to see what is actually available. The configured inference gateway already exposes a live
`GET /v1/models` listing, so VISTA can offer a real dropdown instead of a guessing game, and put
it where the choice is actually made: the chat window.

## What Changes

- Add a backend endpoint that resolves the caller's configured inference endpoint and proxies to
  its `GET /v1/models`, returning the available model IDs (or a reason none are available).
- Add a model-picker dropdown to the chat window's header, next to the project switcher, backed
  by that endpoint.
- Picking a model writes to the same per-user `inference_model` field the settings modal already
  edits — the picker is a faster path to an existing preference, not a new one.
- The settings modal's free-text model field remains as the fallback for endpoints that don't
  support discovery (non-OpenAI-compatible providers).

## Capabilities

### New Capabilities
- `model-picker`: discovering the models available through a researcher's configured inference
  endpoint, and selecting one from the chat window.

### Modified Capabilities
(none — this change reads the existing inference-resolution and credential-reporting behavior in
`zero-config-startup` without altering its requirements)

## Impact

- Backend: new route under `backend/src/vista_backend/api/` (project-scoped, alongside
  `agent.py`), reusing `resolve_inference_target` from `agents/inference.py`. No schema/DB
  migration — `inference_model` already exists on the user row.
- Frontend: new `ui/lib/models.ts` hook and a dropdown component, mounted in `AppTopBar`'s
  `actions` slot on the chat page (`ui/app/page.tsx`), following `ProjectSwitcher.tsx`'s pattern.
  Existing `PUT /users/me` call is reused for persistence, no new mutation.
- Out of scope: per-turn/per-message model override (`AgentRunRequest` is unchanged), and
  discovery for providers outside `_CONFIGURED_ENDPOINT_PROVIDERS` (Azure, bare `anthropic:`,
  `ollama:`) — those keep the existing free-text field.
