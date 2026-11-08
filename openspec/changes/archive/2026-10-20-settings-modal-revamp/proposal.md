## Why

The settings modal has grown into one long form: account, model and endpoint fields at
the top, then a collapsible section per cluster, with a single Save at the bottom. It is
hard to find things in, and it will get worse as more resources arrive. Separately, the
chat-header model picker drifts out of step with Settings: it loads its model list and
current model once, so changing the endpoint, key or model in Settings leaves it stale
until a reload, and an endpoint change keeps a model chosen from the old endpoint.

The inference endpoint is also a free-text URL, while in practice researchers use one of
two AmSC gateways (the legacy i2 LiteLLM, and the Model Access Gateway that replaces it),
OLCF's own Inference Service, or, rarely, something custom.

## What Changes

- **Settings becomes a sectioned modal** with a left nav: Appearance, Agent, and
  Resources. Resources are listed as a tree, institution › facility › resource
  (ORNL › OLCF › Odo, Frontier, Lux; LBNL › NERSC › Perlmutter). Each resource has its own
  page holding its credentials, remote directory, its own Globus connection (Odo and
  Frontier) and its "Show in sidebar" switch. The "Signed in as" block is removed.
- **Settings autosaves.** Text fields save after a short pause, secrets on blur or paste.
  Each field shows its own outcome: a check mark that fades once saved, a cross and the
  reason when a save fails (also marked on its section in the nav), and nothing while
  saving unless it is slow. **BREAKING (UI):** Save and Cancel are removed.
- **Narrow windows** show the nav as a list; a section opens full-width with a back
  button.
- **Inference provider dropdown** replaces the endpoint text field: AmSC i2, AmSC MAG,
  OLCF Inference, and Custom. Only Custom shows a URL field; the i2, MAG and OLCF URLs
  come from presets the backend defines. Each provider keeps its own API key, so switching provider and back loses
  nothing. Azure is not offered; the typed `azure:` model route is dropped.
- **Per-provider default model**: i2 defaults to `claude-sonnet` and OLCF Inference to
  `gpt-oss-120b`; MAG and Custom have no default, and chatting without a model chosen opens the picker instead of sending.
- **The Model field leaves Settings.** The picker is the one place to choose a model. It
  gains a "Use another model…" entry for typing a name, marks a saved model the provider
  does not list, labels the default as "Default (<model>)", and stays in sync with
  Settings. Changing provider clears the chosen model.
- **Model listing works for every provider's base URL**: it asks `<base>/models`, as chat
  asks `<base>/chat/completions`, and falls back to `<base>/v1/models`. i2's URL has no
  `/v1`, MAG's ends in it, and OLCF's carries it mid-path.
- **The rail's HPC section becomes Resources**, with its cards grouped under facility
  headers (OLCF, NERSC), in both the expanded and collapsed rail.

## Capabilities

### New Capabilities
- `settings-modal`: the settings modal's sections and navigation, autosave and the save
  marks on each field, and its layout in narrow windows.
- `inference-providers`: the provider presets, per-provider credentials, how the backend
  resolves the provider, endpoint, key and default model, and how the UI learns them.

### Modified Capabilities
- `model-picker`: the picker becomes the only place to choose a model, stays in sync with
  Settings, accepts a typed name, marks unlisted models, shows the provider's default, and
  holds a send when no model is chosen; discovery runs for every provider preset.
- `hpc-availability`: the modal's per-cluster sections become resource pages under the
  institution › facility tree, saved by autosave; the rail's HPC section is renamed
  Resources and grouped by facility.
- `zero-config-startup`: the default inference target becomes the i2 provider preset with
  its default model; environment overrides stay as a developer path and are shown as such.

## Impact

- **UI**: `components/UserSettingsModal.tsx` (rewritten into sections),
  `components/ModelPicker.tsx`, `components/HpcStatusSection.tsx`, `components/NavRail.tsx`,
  `lib/models.ts`, `lib/user.ts`, `lib/hpc-status.ts` (facility lookup), a new shared store
  for agent settings, `app/globals.css`, and their tests.
- **Backend**: `agents/inference.py` (provider-aware target resolution), `api/models.py`
  (URL normalisation), a new read-only providers endpoint, `db/schemas.py` (new nullable
  columns: `inference_provider`, MAG, OLCF and Custom keys; `inference_api_key` becomes the i2
  key), `config.py` (presets), and the users API for the new fields. New nullable columns
  are added on startup by `db/db.py`; no migration.
- **No new dependencies.** No change to Globus, HPC checks or job submission behaviour.
