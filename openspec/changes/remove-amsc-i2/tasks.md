## 1. Backend: MAG is the default provider

- [x] 1.1 In `backend/src/vista_backend/agents/inference.py`, delete the `i2` preset, drop `"i2"` from `ProviderId`, add `DEFAULT_PROVIDER = "mag"`, rename `_differs_from_i2` to `_differs_from_default`, and make step 3 of `_resolve_from_settings` use the default preset's own key field, falling back to `Settings.openai_api_key` (design D1). Rewrite the module and function docstrings that describe i2. Verify by updating `backend/tests/test_inference_credentials.py`: no-provider resolution gives MAG, `claude-sonnet` and `source == "default"`; a row whose only key is `inference_api_key` resolves to MAG with no key; the env key fallback applies to MAG. Then run `cd backend && uv run --extra dev pytest tests/test_inference_credentials.py`.
- [x] 1.2 In `backend/src/vista_backend/db/schemas.py`, drop `"i2"` from `InferenceProvider` and reword the `inference_provider` and `inference_api_key` docstrings (D4). Add no column changes. Verify that `./scripts/ci-local.sh backend lint` passes, including pyright over every `InferenceProvider` user (`services/campaign.py`, `agents/campaign/agent_tools.py`).
- [x] 1.3 In `backend/src/vista_backend/config.py`, default `openai_base_url` to the MAG URL and add the retired-i2-host validator and `ignored_openai_base_url`. Log the startup warning in `config.py`, after logging is configured (D2). Verify with a test in `backend/tests/test_settings_defaults.py`: `OPENAI_BASE_URL=https://api.i2-core.american-science-cloud.org` yields MAG's URL, `source == "default"`, and the flag set. Update the existing `american-science-cloud.org` assertion there to MAG's host.
- [x] 1.4 In `backend/src/vista_backend/api/settings.py`, add `config_key_set` to `InferenceView` (D4). Verify with a test that `GET /settings/inference` lists exactly `mag, olcf, custom` in that order, and that `config_key_set` follows `inference_api_key`.

## 2. Backend: migrate saved i2 choices

- [x] 2.1 In `backend/src/vista_backend/db/db.py` `init_db`, add the two idempotent UPDATEs on `local_settings` and `campaign_run` (D3). Verify with a test in `backend/tests/test_local_settings.py` that seeds `inference_provider='i2'`, a model, a MAG token and a Custom endpoint, runs `init_db` twice, and finds provider and model NULL with everything else unchanged. Add a `campaign_run` row on `'i2'` that afterwards loads as `CampaignRunPublic` with `provider is None`.
- [x] 2.2 Update the remaining i2 fixtures: `backend/tests/test_local_settings.py` (the `openai_base_url` monkeypatch) and `backend/tests/test_campaign_monitor.py` (`now_on="i2"` → `"mag"`, or another provider the test's intent fits). Verify that `cd backend && uv run --extra dev pytest -m "not live and not hpc and not sandbox"` passes.

## 3. MCP server: remove the OpenMetadata proxy

- [x] 3.1 In `mcp_servers/vista_mcp_server/src/vista_mcp_server/server.py` and `config.py`, remove the `omd` mount, `omd_url` and `omd_api_key`, and drop `"omd"` from the `disable_servers` docstring (D5). Verify that `cd mcp_servers/vista_mcp_server && uv run --extra dev pytest` and `./scripts/ci-local.sh mcp lint` pass, and that `git grep -n -i 'omd_\|openmetadata' -- mcp_servers` is empty.
- [x] 3.2 Remove the OMD block from `.env.sample` and the `VISTA_MCP_OMD_API_KEY` row from `docs/development.md`. Verify that a case-insensitive search for `omd` / `openmetadata` outside lockfiles and `openspec/` finds nothing, and `./scripts/ci-local.sh lint` (docs links) passes.

## 4. UI: no i2 option or copy

- [x] 4.1 In `ui/components/UserSettingsModal.tsx`, remove `i2` from `PROVIDER_KEY_FIELDS` and `keyFieldCopy`, relabel `inference_api_key` in `FIELD_LABELS` as the configured endpoint's key, and replace `keysSet[settings.source === "config" ? "i2" : …]` with `configKeySet`. In `ui/lib/agent-settings.ts`, map `config_key_set` → `configKeySet`. Verify that `cd ui && npm run lint` and `npx tsc --noEmit` pass.
- [x] 4.2 Update `ui/tests/ModelPicker.test.tsx`, `ui/tests/UserSettingsModal.test.tsx`, `ui/tests/agent-settings.test.ts`, `ui/tests/provider-switch-flow.test.tsx` and `ui/e2e-hermetic/fixtures.ts` so the default fixture is MAG. Add an assertion that the provider choice shows no "AmSC i2" option and no i2-core link. Verify that `./scripts/ci-local.sh ui test` passes. That command runs `next build` into `ui/.next`, so don't run it beside a live `launch.sh` stack in the same checkout.

## 5. Docs and samples

- [ ] 5.1 Update `.env.sample` (MAG URL and key source), `docs/installing.md` (MAG out of the box) and `docs/development.md` (the `OPENAI_*` row). Verify that `git grep -n -i 'i2-core\|AmSC i2' -- . ':!openspec/changes/archive' ':!openspec/changes/remove-amsc-i2'` returns nothing.

## 6. End-to-end check

- [ ] 6.1 Hermetic check: `./scripts/ci-local.sh` (all lint and test) passes, and `openspec validate remove-amsc-i2 --strict` passes.
- [ ] 6.2 **Manual / live, not PR CI:** copy a `~/.vista` that was on i2 into a scratch data dir and start `./launch.sh logs`. Confirm that Settings shows AmSC MAG with "Default (claude-sonnet)" and asks for a MAG token. Then enter a real MAG token and confirm a chat turn completes against `mag.genesis.americansciencecloud.org`.
