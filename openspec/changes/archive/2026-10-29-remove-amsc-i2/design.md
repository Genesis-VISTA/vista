## Context

See proposal.md for why. Where VISTA touches i2-core today:

- `agents/inference.py` defines the providers in one place, `PROVIDER_PRESETS`. The `i2` preset
  is the fallback in `_resolve_from_settings` (step 3) and the yardstick in `_differs_from_i2()`,
  which decides whether the installation's `Settings` count as "Custom, from configuration".
  Only i2's key falls back to `Settings.openai_api_key`.
- `config.py` has `openai_base_url` defaulting to the i2 URL.
- `db/schemas.py` uses the `InferenceProvider = Literal["i2", "mag", "olcf", "custom"]` literal in
  `LocalSettingsUpdate` and in the `campaign_run.provider` column type. The legacy
  `inference_api_key` column is documented as i2's key.
- `api/settings.py` builds `keys_set` from the presets. The UI reads `keysSet["i2"]` to show
  whether a key is saved for the "configured" source (`UserSettingsModal.tsx`), and uses `"i2"`
  in `PROVIDER_KEY_FIELDS`, `FIELD_LABELS` and `keyFieldCopy`.
- `vista_mcp_server/server.py` mounts an `omd` proxy to `omd_url` (an i2-core path) when
  `omd_api_key` is set. Nothing else references it. MCP settings use `extra='ignore'`, so a
  leftover `VISTA_MCP_OMD_API_KEY` is harmless once the field is gone.

The constraint is that `db/schemas.py` changes stay additive (AGENTS.md). No column is dropped
or renamed here; only a value is rewritten.

## Goals / Non-Goals

**Goals:**
- No code path, default, link or doc sends a request to i2-core.
- An existing install that was on i2 starts cleanly on MAG and asks only for a MAG token.

**Non-Goals:**
- Reusing the old i2 key for MAG. They are different credentials: an i2 key versus a MAG
  project access token from my.amsc.energy.gov.
- Making `MissingInferenceModel` or the MAG preset's model list any different.

## Decisions

### D1. MAG takes i2's place as the default preset

Delete the `i2` entry and drop `"i2"` from `ProviderId` and `InferenceProvider`. Step 3 of
`_resolve_from_settings` and `InferenceTarget.provider`'s default name a module constant
`DEFAULT_PROVIDER = "mag"` rather than a literal. `_differs_from_i2` becomes
`_differs_from_default`. The `Settings.openai_api_key` fallback moves from i2 to the default
provider, so a developer `.env` with only `OPENAI_API_KEY` keeps working on MAG. The default path
reads the MAG preset's own key field (`inference_mag_api_key`), not the legacy
`inference_api_key`. That is what keeps an old i2 key from being sent to MAG.

*Alternative:* keep an `i2` id as an alias of MAG. That was rejected because it would carry the
i2 key into MAG requests, and the provider would live on in names forever.

### D2. `Settings.openai_base_url` defaults to MAG, and a retired i2 URL is treated as unset

The default becomes `AMSC_MAG_BASE_URL` (`https://mag.genesis.americansciencecloud.org/v1`),
defined once in `config.py` and read by the MAG preset too. A model validator on `Settings`
replaces any `openai_base_url` whose host is `RETIRED_I2_HOST`
(`api.i2-core.american-science-cloud.org`) with that default, and records the ignored URL
(`Settings.ignored_openai_base_url`). `config.py` logs one warning naming
`OPENAI_BASE_URL` / `VISTA_BACKEND_OPENAI_BASE_URL`, straight after it configures logging.
Settings are built and logging is set up at import, once per process, so that is where
startup's other configuration messages already go. The entry point (`api.api:main`) would be
a second place for configuration to report from. Without this,
every developer `.env` copied from today's `.env.sample` would show as "Custom, from configuration"
aimed at a dead host after Oct 31.

Only the URL is ignored. A stale i2 `OPENAI_API_KEY` beside it still falls back (D1) and draws
MAG's 401. The existing `RejectedInferenceCredential` already says what to do about that.

*Alternative:* only warn. That was rejected because the researcher-visible state would be
wrong (Custom, unreachable).

### D3. A startup data fix moves i2 rows to "no choice"

In `init_db`, after `create_all` and `_add_missing_columns`, two idempotent UPDATEs run:

- `local_settings`: `inference_provider = NULL, inference_model = NULL WHERE inference_provider = 'i2'`
- `campaign_run`: `provider = NULL, model = NULL WHERE provider = 'i2'`

NULL already means "follow the default" for both, so no new state is introduced. The model is
cleared because a model chosen on one provider means nothing on another (the same rule as an
explicit switch). This rewrites values, not columns, so `schema_check` is untouched and no reset
prompt fires.

*Alternative:* tolerate `"i2"` at read time. That was rejected: `CampaignRunPublic` and
`LocalSettingsUpdate` validate the literal, so `"i2"` would have to stay in the type. The
resolver already ignores unknown providers (`if provider in PROVIDER_PRESETS`) as a backstop.

### D4. The legacy key column stays, as the key for a configured endpoint

`inference_api_key` keeps its one remaining job: the key for "Custom, from configuration"
(`source == "config"`). Its docstring, `FIELD_LABELS` and the configured-source key hint stop
saying i2. `InferenceView` gains `config_key_set: bool`, and the UI reads that instead of
`keysSet["i2"]`. `keys_set` stays keyed by the remaining presets.

### D5. Delete the OMD proxy outright

Remove the `omd` mount, `omd_url` and `omd_api_key`, and drop `"omd"` from the
`disable_servers` docstring. Remove the `.env.sample` block and the `docs/development.md` row.
Re-pointing `omd_url` at MAG was considered, but MAG documents no MCP or OpenMetadata endpoint,
and a guessed URL would fail at mount time.

## Risks / Trade-offs

- [Researchers on i2 lose chat until they get a MAG token] → the existing "no key" banner and
  the MAG field's "Get a token" link already lead to my.amsc.energy.gov. The release note says so.
- [The `claude-sonnet` name on MAG differs from i2's] → the MAG preset already defaults to it and
  is in use. If the name is wrong, the picker marks it as not listed rather than failing silently.
- [Someone relied on the OMD tools] → only a developer `.env` could enable them. No package or
  launcher sets the key.
- [Old VISTA run against a fixed DB] → it reads NULL as its own default (i2, dead anyway). No
  worse than today.

## Migration Plan

Ship before 2026-10-31. Existing installs migrate at first startup (D3) with no action beyond
entering a MAG token. Developers update `.env` from the new `.env.sample`, though D2 covers one
that wasn't updated. To roll back, revert the commit. The data fix needs no undo.
