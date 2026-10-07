## 1. Backend: providers and storage

- [x] 1.1 Add the provider presets constant (i2, mag, custom: name, URL, default model) beside `resolve_inference_target`, and verify a unit test asserts i2's URL/default and MAG's URL with no default
- [x] 1.2 Add nullable `inference_provider`, `inference_mag_api_key` and `inference_custom_api_key` (both `EncryptedStr`) to `app_user` and the user create/update/self-update/with-config schemas, and verify an existing test database gains the columns on startup and the keys are not plaintext in the DB file (`test_user_tokens.py`)
- [x] 1.3 Make `resolve_inference_target` provider-aware per design.md's resolution order, adding `provider` and `source` to `InferenceTarget` and allowing no model, and verify `test_inference_credentials.py` covers: row provider for each option, the per-provider key choice, `.env` differing from i2 → custom/config, nothing set → i2 with `claude-sonnet`, and an existing row's `inference_api_key` used as the i2 key
- [x] 1.4 Add `MissingInferenceModel`, reported in chat as a named condition like `MissingInferenceCredential`, and verify a chat test on MAG with no model gets that message rather than an internal error
- [x] 1.5 In the `PUT /users/me` handler, clear `inference_model` when `inference_provider` changes and the same update does not set a model, and verify both cases in a users API test
- [x] 1.6 Add `GET /users/me/inference` (options, effective provider/source/model, is-default, key-set booleans, no secrets), plus its UI proxy route, and verify an API test checks the shape and that no key value appears in the response
- [x] 1.7 Normalise the base URL in `api/models.py` (strip a trailing `/v1` and slash before appending `/v1/models`), list using the resolved provider's URL and key, and verify `test_models_api.py` lists models for a base URL with and without `/v1`
- [x] 1.8 Make citation extraction use the resolved target, and skip extraction with the existing notice when there is no model, and verify a test in `test_indexer_paths.py` (or alongside it) covers MAG with no model
- [x] 1.9 Add the `olcf` preset (OLCF Inference, `https://s3m.olcf.ornl.gov/olcf/open/v1/inference`, default `gpt-oss-120b`, ordered before Custom) and a nullable `inference_olcf_api_key` (`EncryptedStr`) on `app_user`, the user schemas and the UI user types, plus the provider lists in the UI test and hermetic fixtures. Verify tests that the preset has that URL and default, an existing database gains the column, the key is not plaintext in the DB file, a row on `olcf` uses that key and not the Odo/Frontier S3M tokens, and `/users/me/inference` lists the four options in order
- [x] 1.10 Change model listing in `api/models.py` to `<base>/models`, falling back once to `<base>/v1/models` on a 404, replacing the strip-trailing-`/v1` rule. Verify `test_models_api.py` lists models for OLCF's mid-path `/v1` base (asking `…/v1/inference/models`), for a base ending in `/v1`, and for a base with no `/v1` whose `/models` answers 404

## 2. UI: agent settings store and model picker

- [x] 2.1 Add `lib/agent-settings.ts`, a module-level store over `/users/me/inference` with `useAgentSettings()` and `refreshAgentSettings()`, and verify a unit test that a refresh updates every subscriber
- [x] 2.2 Make `qualifyModelInput` always add `openai:` (colons included), and verify a unit test with `anthropic.claude-sonnet-v1:0`
- [x] 2.3 Rework `ModelPicker` to read the label from the store ("Default (<model>)", or a chosen model marked "not listed by <provider>"), refetch the list on open and when provider/endpoint/key change, and refresh the store after a selection, and verify `ModelPicker.test.tsx` covers each label and the refetch after a provider change without a remount
- [x] 2.4 Add the always-present "Use another model…" entry with a text input, and verify a test that a typed name is saved as `openai:<name>`
- [x] 2.5 Replace the stale "click your name in the bottom-left corner" hint with text pointing to the picker's typed entry, and verify by test that the unavailable/error states show it
- [x] 2.6 Hold a send when the store says there is no effective model: keep the composer text and open the picker with a "choose a model first" hint, and verify by a test of the chat page (or its composer hook) that no run starts

## 3. UI: settings modal structure

- [x] 3.1 Add `RESOURCE_TREE` (ORNL › OLCF › odo, frontier, lux; LBNL › NERSC › perlmutter) to `lib/hpc-status.ts`, and verify `hpc-status.test.ts` asserts every `HPC_CLUSTERS` entry appears exactly once and no facility is empty
- [x] 3.2 Split `UserSettingsModal` into a navigation (Appearance, Agent, the resource tree with status dots and hidden markers) and one section at a time, opening on Agent, or on `initialCluster`'s section; remove the "Signed in as" block. Verify `UserSettingsModal.test.tsx` covers default and deep-link opening and the hidden marker
- [x] 3.3 Build the Appearance section from the existing `AppearanceSetting`, and verify the existing theme tests still pass
- [x] 3.4 Build the Agent section: the provider choice from the store's options, a key field per provider, an endpoint field only for Custom, the "from configuration" label when the source is config, and no Model field. Verify by test that switching provider shows that provider's key field and that only Custom shows a URL
- [x] 3.5 Build each cluster's section from the existing fields (institution › facility line, status, "Show in sidebar", credentials, remote directory, and Odo's and Frontier's own Globus connect), and verify the existing per-cluster field tests pass against the new layout
- [x] 3.6 Add the narrow layout (container query at about 720 px: list, then full-width section with a back control), and verify with a Playwright screenshot at a narrow width in the hermetic shots suite

## 4. UI: autosave

- [x] 4.1 Add a field save hook (about 800 ms debounce for text; blur/paste for secrets; immediate for switches/choices; serialised per field; flush on close), and verify unit tests with fake timers for each trigger and for flush on close
- [x] 4.2 Add the modal-level save tracker and the indicator by the title (saving, saved fading, failed), with error lines under failed fields and jump-to-field on the failed indicator, and verify by test a failure, then a successful retry clearing it
- [x] 4.5 Replace the title indicator with a mark on each field (nothing while saving, a spinner past about a second, a check that fades after about two seconds or on the next edit, a cross while failed), a cross on the navigation entry of a section holding a failed field that focuses it when chosen, and one hidden live region naming each outcome. Verify by test: a check on the saved field only, the spinner on a slow save, a cross and reason on failure, the navigation cross after leaving the section and the focus on return, and everything cleared by a successful retry
- [x] 4.3 Wire each field to the hook; trigger the per-cluster recheck after a credential field saves, `refreshHpcStatus()` after a visibility change, and `refreshAgentSettings()` after provider/key/endpoint saves; remove Save/Cancel. Verify by test that pasting an Odo token rechecks only Odo
- [x] 4.4 Verify the provider-change flow end to end in a UI test: choosing MAG clears the picker label to "no model", and switching back to i2 shows "Default (claude-sonnet)" with the original key intact

## 5. UI: rail

- [x] 5.1 Rename the rail's HPC section to Resources and group its cards under facility headers from `RESOURCE_TREE`, omitting a facility with no visible cluster, and verify `HpcStatusSection.test.tsx` covers the grouping and Perlmutter hidden → no NERSC header
- [x] 5.2 Group the collapsed rail's short labels under short facility labels, keeping accessible names like "Frontier: Ready", and verify by test

## 6. Wrap-up

- [x] 6.1 Update the comments and docs that describe the old form, the Model field, or the endpoint text field (`UserSettingsModal`, `ModelPicker`, `lib/models.ts`, `.env.sample`, `docs/` where they mention it), and verify a grep for "Model field in Settings" and "Signed in as" finds nothing stale
- [x] 6.2 Run `./scripts/ci-local.sh` (backend, ui, mcp lint and test) and verify it passes
- [ ] 6.3 Launch the app (`./launch.sh logs`), walk through: fresh i2 default, switching to MAG and back, OLCF Inference with an S3M token (its default and its model list), a Custom endpoint ending in `/v1`, autosave of a token with its check mark, the narrow layout and the grouped rail. Capture screenshots
