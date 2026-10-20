All tests here are hermetic (scripted model, in-memory DB, mocked fetch); nothing needs a key
or live provider. Run the backend ones with `./scripts/ci-local.sh backend test` and the UI
ones with `./scripts/ci-local.sh ui test`.

## 1. Backend: a failed turn carries its reason

- [ ] 1.1 Add `describe_run_failure(exc)` to `backend/src/vista_backend/agents/inference.py`
  (design decision 2: `detail` passthrough, `ModelHTTPError` summary from the OpenAI-shaped,
  `message`, `detail` or string body, `str(exc)` fallback, length cap). Verify with unit tests in `backend/tests/test_inference_credentials.py` covering a 404
  with an OpenAI-shaped body, a body with no message, a plain exception, an empty-message
  exception, and an over-long body.
- [x] 1.2 Add `RejectedInferenceCredential(model_name)` to `agents/inference.py`, with `detail`
  from `rejected_credential_detail`. In `agents/agents.py` raise it on a 401/403
  `ModelHTTPError` instead of yielding the log line and empty result. Verify by rewriting
  `test_rejected_credential_is_reported_not_a_broken_stream` to assert the stream ends with
  `run_finished` `state: "failed"` whose `error` is the rejected-credential message, and that
  `test_other_provider_errors_still_propagate` still passes.
- [ ] 1.3 In `services/chat_run.py`, set `error: describe_run_failure(exc)` on `run_finished`
  when the run fails, keeping the existing ERROR `log` event. Verify with a new
  `test_a_failed_run_reports_why` in `backend/tests/test_chat_run.py`: an agent that raises
  ends with `run_finished` carrying the reason both to a live subscriber and in the saved row's
  events.

## 2. Backend and proxy: refusals say which they are

- [ ] 2.1 Register `RejectedInferenceCredential` with the handler in `api/api.py` and add
  `code: "inference_not_configured"` to its 409 body. Verify by extending
  `test_chat_with_no_credential_is_reported_not_crashed` and
  `test_chat_with_no_model_is_reported_not_crashed` (`test_inference_credentials.py`) to assert
  the `code`, and that the model picker tests in `backend/tests/test_models_api.py` still pass.
- [ ] 2.2 Answer `RunBusy` in `api/agent.py` with `409 {"detail", "code": "run_busy"}`. Verify
  by extending `test_a_send_while_busy_is_a_409_and_changes_nothing`
  (`backend/tests/test_chat_run_api.py`) to assert the `code`.
- [ ] 2.3 In `ui/app/api/chat/route.ts`, pass a JSON error body through with its status, and
  wrap only a non-JSON body as `{ok: false, error}`. Verify with a new `POST /api/chat` block in
  `ui/tests/chat-run-routes.test.ts`: a JSON 409 arrives with `detail` and `code` intact, and a
  plain-text 500 arrives wrapped.

## 3. UI: the conversation shows it

- [ ] 3.1 In `ui/lib/run-renderer.ts`, draw `"Agent run failed: <error>"` for a failed
  `run_finished` with a non-empty string `error`, and the plain note otherwise. Verify by
  extending the finish-note cases in `ui/tests/run-renderer.test.ts` with an `error`, a
  non-string `error`, and none.
- [ ] 3.2 Extract the decision on a refused send into a small pure helper in `ui/lib` (input:
  status and parsed body; output: attach to the running turn, or the message to show), so it can
  be tested without the page. Verify with a unit test: `run_busy` attaches; a 409 with
  `inference_not_configured`, a 409 with no code, and a 500 each yield their message.
- [ ] 3.3 Use the helper in `ui/app/page.tsx`'s send: on `run_busy` keep today's attach; on any
  other refusal drop the unsent bubble, restore the text into the input, and add the message as
  a note. Verify with `./scripts/ci-local.sh ui lint` and the UI tests.
- [ ] 3.4 Manual check in the running app (`./launch.sh logs`, not in CI): with the key cleared,
  Send shows the "No inference API key…" note and the text is back in the input; with a wrong
  model chosen through Custom, the turn ends "Agent run failed: The provider answered 404…", and
  reopening the conversation shows the same note.
