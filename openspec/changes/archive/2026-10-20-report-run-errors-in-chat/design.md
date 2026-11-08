## Context

A conversation's turn is run by the backend (`services/chat_run.py`), and every page only
watches its events. The run ends with `run_finished {state}`, and that event is saved with the
others (`compact_events` keeps its data untouched), so a page that opens the conversation later
redraws the turn from the same events. One renderer (`ui/lib/run-renderer.ts`) draws all three
cases: the send's stream, a re-attach, and a replay.

Today:

- `chat_run.py` catches any exception from the agent, appends a `log` ERROR event
  `"Turn failed: {exc}"`, and ends with `run_finished {state: "failed"}`. The renderer sends
  `log` events to the log panel only, and draws `FINISH_NOTES.failed` ("Agent run failed").
- `agents.py` catches a 401/403 `ModelHTTPError` from the provider, yields a `log` line with
  `rejected_credential_detail(...)` and an empty `ProjectAgentResultEvent`, and returns. The
  turn ends `done` with no bubble.
- Before a run starts, `api/agent.py` calls `require_inference_credential`, whose
  `MissingInferenceCredential` / `MissingInferenceModel` are turned into
  `409 {"detail": ...}` by the handler in `api/api.py`. A busy conversation (`RunBusy`) is
  turned into `409 {"detail": ...}` by `api/agent.py`.
- The Next proxy (`ui/app/api/chat/route.ts`) answers a non-OK backend response with
  `{ok: false, error: <backend body as text>}`, so the page receives the backend JSON as a
  string. `page.tsx` handles any 409 by attaching to a running turn.

## Goals / Non-Goals

**Goals:**

- One path for "why did this turn fail": whatever ends a turn as failed reaches the
  conversation through `run_finished`, and replays get it for free.
- The page decides on a refused send from an explicit code, never from status or wording alone.

**Non-Goals:**

- Changing the non-chat callers of the agent (campaign subagents, report and skill authoring,
  debates). They do not use `ProjectAgent.run_stream`.
- Reworking the log panel or the `log` event; the ERROR log line stays as it is.

## Decisions

### 1. The reason travels on `run_finished`, as `error`

`run_finished` gains an optional string `error` when `state` is `failed`. `chat_run.py` sets it
from `describe_run_failure(exc)` in the same `except` that marks the run failed.

*Alternative:* the renderer remembers the last ERROR `log` event and shows it on a failure. No
backend change, but it relies on log ordering and text, and any unrelated ERROR log (a tool
warning, an MCP server message) could be shown as the reason.

### 2. `describe_run_failure` in `agents/inference.py`

It sits next to `rejected_credential_detail`, because both turn a provider failure into words
for the researcher.

- An exception with a researcher-facing `detail` (`MissingInferenceCredential`,
  `MissingInferenceModel`, the new `RejectedInferenceCredential`) returns that `detail`.
- `ModelHTTPError` returns
  `"The provider answered <status> for model '<model>': <message>"`, where `<message>` is the
  provider's own: the OpenAI-shaped `body["error"]["message"]`, else `body["message"]` or
  `body["detail"]`, else a string body. When none is found the sentence ends after the model.
- Anything else returns `str(exc)`, or the exception's type name when that is empty.
- The result is capped (about 500 characters) so a pathological body cannot flood the note.

The full exception and traceback still go to the backend log via `log.exception`.

### 3. A rejected key raises `RejectedInferenceCredential`

`agents.py` raises `RejectedInferenceCredential(model_name)` (its `detail` is
`rejected_credential_detail(model_name)`) instead of yielding an empty result. In a conversation
this fails the turn through decision 1, with the existing message. The exception is also
registered with the `api/api.py` handler, so the non-conversation `agent.run` path answers a
409 naming the setting rather than a 500.

The 401/403-only filter stays where it is: anything else from the provider still propagates and
is reported by decision 2 as a provider error, never as a configuration problem.

*Alternative:* let the 401/403 `ModelHTTPError` propagate and map it in `describe_run_failure`.
That splits the "is this a credential problem" decision across two modules, and turns the
non-conversation path into a 500.

### 4. Refusals carry a `code`

- The `api/api.py` handler answers `409 {"detail", "code": "inference_not_configured"}` for a
  missing credential, a missing model, and a rejected credential. Other endpoints that raise
  these through the shared handler (the model picker, debates) gain the same additive field.
- `RunBusy` answers `409 {"detail", "code": "run_busy"}`. `HTTPException` cannot carry a
  sibling field without changing `detail` into an object, so `api/agent.py` returns a
  `JSONResponse` (or a handler for `RunBusy` beside the credential one).
- Both stay 409, consistent with the model picker and debates, which already use 409 for the
  credential case.

*Alternatives:* a different status for the credential case (breaks that consistency), or
matching on the `detail` text (breaks the page when the message is reworded).

### 5. The proxy passes a JSON error through

`route.ts`, on a non-OK backend response whose content type is JSON, returns the backend's body
and status unchanged. Only a non-JSON body is wrapped as `{ok: false, error: <text>}`, as today.
The page already reads `errData?.error || errData?.detail`, so both shapes keep working.

### 6. The page handles a refused send in one place

In `page.tsx`, any non-OK response from `/api/chat` is read once:

- `code === "run_busy"`: the current behaviour (drop the bubble, restore the text, attach).
- anything else: drop the unsent bubble, restore the text into the input, and add a note with
  the message (`detail`, else `error`, else the status). The researcher fixes the setting and
  presses Send again.

Restoring the text for every refusal, not only 409s, keeps the behaviour single and matches the
`chat-runs` scenario "Other refusals are not mistaken for busy".

### 7. The renderer appends the reason

On `run_finished` with `state === "failed"` and a non-empty string `error`, the note is
`"Agent run failed: <error>"`. Without one it stays `"Agent run failed"`, so turns saved before
this change still draw.

## Risks / Trade-offs

- [A provider message may include details the researcher does not need, such as request ids]
  → It is the provider's own message, already shown in the log panel today; the cap keeps it
  bounded.
- [An exception wrapped in an `ExceptionGroup` (from the MCP client's task group) would be
  described by its group text, "unhandled errors in a TaskGroup"] → Not handled: nothing in a
  turn's path is known to raise one (`StreamMerger` re-raises the original). Add the case with
  a reproduction if that text is ever seen.
- [The rejected-key turn now shows the red "failed" status instead of "done"] → Intended: the
  turn did not answer, and the status now agrees with the note.
- [`test_rejected_credential_is_reported_not_a_broken_stream` asserts an `agent_run_result`]
  → It is rewritten to assert a failed `run_finished` whose `error` is the rejected-credential
  message.
