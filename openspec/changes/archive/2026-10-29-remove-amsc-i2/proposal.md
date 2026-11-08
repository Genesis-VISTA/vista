## Why

AmSC is deactivating the i2-core API ("Old MAG", `https://api.i2-core.american-science-cloud.org`)
on 2026-10-31 and asks every client to move to the production MAG gateway
(`https://mag.genesis.americansciencecloud.org`). VISTA ships i2 as its out-of-the-box
inference provider and proxies the AmSC OpenMetadata catalog through i2-core, so after that date
a fresh install, and any researcher who never changed provider, cannot chat.

## What Changes

- **BREAKING** Remove the AmSC i2 inference provider. Settings offers AmSC MAG, OLCF Inference
  and Custom, and AmSC MAG becomes the default for a researcher who has not chosen one.
- The installation default endpoint (`OPENAI_BASE_URL` / `Settings.openai_base_url`) becomes MAG's.
  A configuration that still names the retired i2 endpoint is ignored with a warning, so it does
  not appear as "Custom, from configuration" pointing at a dead host.
- A researcher whose saved provider is i2, and a campaign run recorded on i2, are moved to "no
  choice" at startup. They land on the MAG default and are asked for a MAG project access token.
  Their old i2 key is never sent to MAG.
- **BREAKING** Remove the OpenMetadata (`omd`) MCP proxy, including `VISTA_MCP_OMD_API_KEY` and
  `omd_url`. Its only endpoint was on i2-core, and MAG documents no replacement.
- Update `.env.sample`, `docs/installing.md`, `docs/development.md`, the Settings copy, and the tests
  and fixtures that name i2.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `inference-providers`: the provider list drops AmSC i2. A key saved before provider choice is
  no longer i2's. A saved i2 choice migrates to the default.
- `zero-config-startup`: the default inference target is AmSC MAG, and a configured i2 endpoint is
  treated as unset.
- `model-picker`: the "Default (claude-sonnet)" scenario is on AmSC MAG rather than i2.

## Impact

- Backend: `agents/inference.py` (`PROVIDER_PRESETS`, resolution), `config.py`,
  `db/schemas.py` (`InferenceProvider` literal and docstrings; no column changes),
  `db/db.py` (startup data fix), `api/settings.py` (`keys_set`).
- MCP: `vista_mcp_server/config.py` and `server.py` (the `omd` mount).
- UI: `UserSettingsModal.tsx`, plus tests and the hermetic e2e fixtures.
- Docs: `.env.sample`, `docs/installing.md`, `docs/development.md`.
- Researchers on i2 need a MAG project access token from `https://my.amsc.energy.gov/`.

## Non-goals

- No replacement metadata catalog. If MAG or AmSC later hosts OpenMetadata over MCP, that is a
  separate change.
- No change to MAG, OLCF Inference or Custom behaviour, model listing, or OLCF IRI endpoints
  (`amsc-*.s3m.olcf.ornl.gov` are unrelated to i2-core).
- No schema change: `inference_api_key` stays, still the key for an endpoint set in configuration.
- No VISTAGuard work.

Related: `openspec/specs/inference-providers`, `openspec/specs/zero-config-startup`,
`openspec/specs/model-picker`, archived `2026-10-20-settings-modal-revamp`.
