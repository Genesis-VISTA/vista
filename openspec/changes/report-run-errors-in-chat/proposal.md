## Why

When a chat turn cannot reach the model, the researcher is not told why
([GitLab #80](https://gitlab.com/amsc2/genesis/vista/-/issues/80), from the Genesis Hackathon
feedback: "Show model-config errors in the UI, not only raw logs"). Retested 2026-10-19:

- A wrong model name ends the turn with only "Agent run failed". The provider's error reaches
  the log panel and the backend log, never the conversation.
- With no API key, Send does nothing visible. The backend refuses with a 409 naming the
  setting, but the chat page treats every 409 as "another view is already running a turn"
  and tries to attach to a run that does not exist.
- A key the provider rejects (401/403) ends the turn as a successful, empty one. The chat
  shows nothing, and only the log panel names the setting.

The last two break `zero-config-startup`'s requirement that an absent or rejected credential
is reported as a named condition; the first is not specified anywhere.

## What Changes

- A failed turn carries its reason to the conversation, live and when the conversation is
  reopened later: "Agent run failed: <reason>". For a provider error the reason names the
  model, the HTTP status and the provider's own message rather than a raw response dump.
- A credential the provider rejects fails the turn with the existing message naming
  Settings, instead of ending it as an empty success.
- The chat endpoint's two conflict refusals become distinguishable by a `code`:
  `run_busy` (the conversation already has a turn running) and `inference_not_configured`
  (no API key, or no model chosen). Both keep HTTP 409.
- The chat page attaches to a running turn only on `run_busy`. Any other refused send shows
  the backend's message in the conversation and puts the unsent text back in the input.

Out of scope: showing the reason on the conversation's status dot or in the nav rail, a
pre-send check for a key or model, and the Hypothesis Lab and debates.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `chat-runs`: a failed turn shows its reason (new requirement); the busy refusal is
  identifiable as such (One active run per conversation).
- `zero-config-startup`: a missing or rejected credential is shown in the conversation, with
  the researcher's text kept (Missing inference credentials are a reported state).

## Impact

- Backend: `services/chat_run.py` (failure reason on `run_finished`), `agents/inference.py`
  (failure summary, rejected-credential exception), `agents/agents.py` (rejected key fails the
  turn), `api/agent.py` and `api/api.py` (409 codes).
- UI: `ui/app/api/chat/route.ts` (pass the backend's JSON error through),
  `ui/app/page.tsx` (409 handling), `ui/lib/run-renderer.ts` (failure note).
- Wire format: `run_finished` gains an optional `error`; the chat endpoint's 409 bodies gain
  `code`. Both are additive; saved turns without `error` draw as before.
