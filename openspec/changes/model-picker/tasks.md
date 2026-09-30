## 1. Backend: model-listing endpoint

- [x] 1.1 Add `GET /projects/{project_name}/models` (new `backend/src/vista_backend/api/models.py`), calling `resolve_inference_target(user)` and, when the target's `uses_configured_endpoint` is true, issuing `GET {base_url}/v1/models` with the resolved credential; verify by exercising the route in `backend/tests/test_models_api.py` (added in 2.1).
- [x] 1.2 Handle the no-credential case: return a response the frontend can render as guidance, reusing `MissingInferenceCredential`'s existing message/`SETTINGS_LOCATION`, instead of a 500; verify with a unit test asserting the returned text matches `MissingInferenceCredential(...).detail`.
- [x] 1.3 Handle a resolved target outside `_CONFIGURED_ENDPOINT_PROVIDERS` (`uses_configured_endpoint=False`): return a "discovery unavailable" response instead of attempting a call the endpoint may not support; verify with a unit test.
- [x] 1.4 Register the new router in `backend/src/vista_backend/api/api.py` alongside the other project-scoped routers; verify `GET /openapi.json` on a running instance lists the new path.

## 2. Backend: tests

- [x] 2.1 Add `backend/tests/test_models_api.py`, `pytest.mark.unit`, mocking the outbound call to the gateway (no real network) and covering: successful listing returns the mocked IDs; no credential configured returns the missing-credential response from 1.2; a provider outside `_CONFIGURED_ENDPOINT_PROVIDERS` returns the discovery-unavailable response from 1.3. Verify with `cd backend && uv run --extra dev pytest tests/test_models_api.py -m unit`.
- [x] 2.2 Add one `pytest.mark.live` test in the same file that calls the real configured gateway's `/v1/models` end-to-end, following `docs/validation-lane.md`'s pattern for live-only tests (excluded from the default PR filter `not live and not hpc and not sandbox`; run via `./scripts/nightly-validation.sh` or manually).

## 3. Frontend: data layer

- [x] 3.1 Add `ui/app/api/projects/[name]/models/route.ts` proxying to the new backend endpoint (project-scoped, matching the backend route's own path — corrected from this task's original generic `ui/app/api/models/route.ts`), following `ui/app/api/projects/[name]/members/route.ts`'s pass-through shape.
- [x] 3.2 Add `ui/lib/models.ts`: a hook mirroring `ui/lib/projects.ts`'s fetch-once-per-mount pattern, exposing loading / list / missing-credential / discovery-unavailable states distinctly (not collapsed into a single "empty list") via a `ModelsState` discriminated union.

## 4. Frontend: the picker

- [x] 4.1 Add `ui/components/ModelPicker.tsx` following `ProjectSwitcher.tsx`'s interaction pattern (local `open` state, `pointerdown`-outside-close, `Escape`-to-close, `role="listbox"`/`role="option"`); selecting an option calls the same user-update path `UserSettingsModal.tsx` already uses to write `inference_model`.
- [x] 4.2 Add `.model-picker-*` styles to `ui/app/globals.css`, matching `.project-switcher-*`'s token usage (`--brand`, `--line`, `--r-control`, etc.) rather than introducing new visual language.
- [x] 4.3 Mount `<ModelPicker />` in the chat page's `AppTopBar` `actions` (`ui/app/page.tsx`, next to the existing "New conversation" button). Verified structurally (tsc + eslint clean, mounted unconditionally in `chat-header-actions`); a live browser check against the running dev stack was not completed this session — see note below.
- [x] 4.4 Render the two degraded states from 3.2: missing credential shows the backend's own guidance text (the 409 `detail`, which already contains `SETTINGS_LOCATION`); discovery-unavailable shows an authored hint pointing at the free-text field in `UserSettingsModal.tsx` instead of an empty or fake list.

## 5. Frontend: tests

- [x] 5.1 Add `ui/tests/ModelPicker.test.tsx` following an existing component test's setup (e.g. `ui/tests/ElicitationModal.test.tsx`), covering: renders a fetched list; selecting an option triggers the update call and closes the dropdown; missing-credential state renders guidance text; discovery-unavailable state does not render a list styled as live/complete; Escape and click-outside close the dropdown. 7/7 pass; full suite (`npm run test`) 109/109 pass, no regressions.

## 6. Verification

- [ ] 6.1 Run `./scripts/ci-local.sh backend test` and `./scripts/ci-local.sh ui test` and confirm both pass hermetically (no AmSC key required) before requesting review.
- [ ] 6.2 Manually verify end-to-end against the real configured gateway (as in this change's feasibility check): open the chat window, confirm the picker lists real models, select one, confirm it persists (`PUT /users/me`) and the settings modal's `inference_model` field reflects the same value.
