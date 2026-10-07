## Context

See proposal.md for the motivation. The current state that shapes the approach:

- `UserSettingsModal.tsx` is one form with a draft copy of every field and a single
  `save()` that diffs against the loaded user and sends one `PUT /users/me`. Afterwards it
  rechecks only the clusters whose credential fields changed (`CREDENTIAL_FIELDS`).
- `ModelPicker.tsx` fetches the current model once on mount. `useAvailableModels` fetches
  the list on mount and whenever the project changes. Its `refresh()` is never called, and
  nothing tells the picker that Settings saved.
- `resolve_inference_target(user)` takes the row's model, base URL and key, falling back to
  `Settings` (environment / `.env`) for each. Only an `openai`-family prefix uses the
  configured endpoint and key (`uses_configured_endpoint`); `azure:` and the rest go to
  PydanticAI's own environment-configured providers.
- `api/models.py` always appends `/v1/models` to the base URL, while chat sends the base
  URL as given.
- `db/db.py` adds missing nullable columns to existing tables at startup, so new nullable
  columns need no migration.
- A cluster's institution and facility appear only as display text
  (`HpcStatusSection.SUBTITLES`).

Canvas with the agreed layouts:
https://claude.ai/artifact/DXBpde9TDS5rwo1WSEVpQM (row "A, revised").

## Goals / Non-Goals

**Goals:**
- One source of truth for the provider presets (the backend), read by the UI.
- The picker and Settings read the same agent-settings state, so neither can show a stale
  value.
- Autosave that never saves a half-typed secret, and that still triggers the targeted
  cluster recheck.

**Non-Goals:**
- Azure, or any non-OpenAI-compatible provider, as a provider option. `azure:` stays
  reachable only through `VISTA_BACKEND_MODEL`.
- The MAG i2-client proxy / OAuth flow. MAG is reached with a project access token as a
  Bearer key.
- Moving the cluster grouping to the backend, or making resources configuration-driven.
- Any change to HPC checks, Globus or job submission behaviour.

## Decisions

### Provider presets live in the backend

A constant, next to `resolve_inference_target`, maps each provider id to its display name,
base URL and default model:
- `i2`: `https://api.i2-core.american-science-cloud.org`, `claude-sonnet`;
- `mag`: `https://i2-api.staging.american-science-cloud.org/v1`, no default;
- `olcf` (OLCF Inference): `https://s3m.olcf.ornl.gov/olcf/open/v1/inference`,
  `gpt-oss-120b`;
- `custom`: URL from the row, no default.

The options are offered in that order, Custom last. OLCF's key is an S3M project access
token, minted on myOLCF for a project granted access to the Inference Service. It is
stored apart from the Odo and Frontier S3M tokens: S3M tokens carry per-token
permissions, and nothing confirms that a compute token is also accepted for inference.

The backend needs the presets anyway: it turns a provider into a URL, a key and a default
model. Keeping them only in the UI would split that knowledge. Alternative: a UI constant.
Rejected, because the backend would have to trust a URL sent by the client.

### Storage: an explicit provider, and a key column per provider

New nullable columns on `app_user`:
- `inference_provider` (`i2` | `mag` | `olcf` | `custom`);
- `inference_mag_api_key` (`EncryptedStr`);
- `inference_olcf_api_key` (`EncryptedStr`);
- `inference_custom_api_key` (`EncryptedStr`).

The existing `inference_api_key` becomes the i2 key, so every researcher who saved a key
today, almost all on i2, keeps working. The existing `inference_base_url` becomes Custom's
endpoint and is ignored otherwise.

Alternatives:
- Work out the provider from the saved URL. Rejected: with per-provider keys VISTA must
  know which key is which, and MAG's URL is hidden and will move from staging to
  production.
- One JSON credentials column. Rejected: `EncryptedStr` already works per column, and the
  users API is field-oriented.

### Resolution order

`resolve_inference_target(user)` becomes provider-aware:

1. **The row names a provider.** Use that preset's URL (Custom: `inference_base_url`), that
   provider's key column, and the row's model, falling back to the preset's default (none
   for MAG and Custom).
2. **No provider, and the installation's configuration differs from the i2 preset.** That
   is, `Settings.openai_base_url` or `Settings.model` is not i2's. Use `Settings` as today,
   and report the provider as `custom` with source `config`.
3. **Otherwise, i2.** Use the i2 preset, the row's `inference_api_key` falling back to
   `Settings.openai_api_key`, and the row's model falling back to `claude-sonnet`.

`InferenceTarget` gains `provider` and `source` (`user` | `config` | `default`) and allows
`model` to be absent. When the model is absent, chat raises a named
`MissingInferenceModel`, reported like `MissingInferenceCredential`. This is the backstop
behind the UI holding the send. `citation_credentials` keeps building on the resolved
target; with no model, it returns no credentials, which the indexer already reports.

### Changing provider clears the model, server-side

In the `PUT /users/me` handler: if `inference_provider` changes and the same update does
not set `inference_model`, set the model to null. Doing it on the server keeps every client
consistent, and avoids a second request racing the first.

### A read-only "agent settings" view for the UI

`GET /users/me/inference` returns:
- the provider options: id, display name, whether it takes a URL, default model;
- the effective provider, its source, and the effective model with whether it is a
  default;
- whether a key is set for each provider (booleans only).

Settings still reads the key values themselves through the existing full user view. The
picker needs no secrets. Alternative: put this into `UserPublicWithConfig`. Rejected,
because that view is secret-bearing and the picker shouldn't need it.

### Shared UI store for agent settings

A small module-level store, the same pattern as `lib/hpc-status.ts`, holds the
`/users/me/inference` view and exposes a `useAgentSettings()` hook plus
`refreshAgentSettings()`. Settings calls the refresh after a provider, key, endpoint or
model save; the picker selection writes and then refreshes. The picker's model list is
keyed by (project, provider, endpoint, key-present), and it is also refetched each time the
menu opens. The store keeps the label right without a reload; the refetch on open catches
anything else.

### Typed model names always mean the chosen provider

`qualifyModelInput` always prefixes `openai:`, even when the name contains a colon.
PydanticAI splits on the first colon only, so `openai:anthropic.claude-…-v1:0` reaches the
endpoint intact. The `openai:` prefix stays an internal routing detail: the `test` stub and
`ollama:` in development still depend on prefixes.

### Listing URL

`api/models.py` asks `<base>/models`, the same convention the OpenAI client uses for chat
(`<base>/chat/completions`), so listing reaches the models wherever chat reaches them. If
that answers 404, it asks `<base>/v1/models` once. Any trailing slash is dropped first.

The presets need this: i2's base has no `/v1`, MAG's ends in it, and OLCF's carries it
mid-path (`…/open/v1/inference`), so no rule about a trailing `/v1` fits all three. The
fallback keeps i2 working without depending on LiteLLM serving `/models` unprefixed.

Alternative: strip a trailing `/v1` and append `/v1/models` (what group 1 first did).
Rejected: for OLCF it asks `…/v1/inference/v1/models`.

### Autosave

Each field is bound to a small save hook. Plain text debounces by about 800 ms; secret
fields save on blur and on paste; switches and choices save at once. Each save sends a
single-field `PUT /users/me`. Saves of the same field are serialised, so a slow response
cannot overwrite a newer value. Saves of different fields may run in parallel.

The save state is kept per field: in flight, slow (in flight past about a second), saved
(for about two seconds) or failed, with the failure's reason. Each field draws its own mark
from it: nothing while in flight, a spinner once slow, a check when saved, a cross when
failed, with the reason beneath. The navigation marks a section holding a failed field, and
choosing it focuses that field. One visually hidden live region announces each outcome by
field name. A save of a field in
`CREDENTIAL_FIELDS` triggers that cluster's recheck, as Save does today; a change to
`hpc_hidden_clusters` triggers `refreshHpcStatus()`. On close, pending debounced saves are
flushed rather than dropped.

Alternative: keep one Save. Rejected by the researcher; the targeted recheck is preserved
per field instead.

Alternative: one indicator by the title (saving, saved, failed). Tried and rejected: a
one-field save to a local backend completes within a frame, so "saving" only flickered, and
the indicator never said which field it meant. A save that fails after the modal closes has
nowhere to show; that needs the backend to fail at the moment of closing, and is accepted.

### Navigation tree and rail grouping from one lookup

`lib/hpc-status.ts` gains a `RESOURCE_TREE`: institutions in order, each with facilities in
order, each with its clusters. Both the settings navigation and the rail's facility headers
are built from it, so their order cannot drift. The tree only lists clusters in
`HPC_CLUSTERS`, so there are no empty facilities.

### Modal layout

A two-column layout: navigation about 244 px wide, then the section. Below about 720 px of
modal width, it switches to one column: the list, then the section with a back control.
This uses a container query on the modal, so the Electron window and a browser window
behave the same.

The modal opens on the Agent section, or on a cluster's section when opened from its card
(`initialCluster`).

## Risks / Trade-offs

- **[Autosave sends a wrong value straight away]** A mistyped remote directory is saved at
  once. → It was already one click from being saved, and the settings check reports an
  unusable value on the card, as today.
- **[Many small requests]** → Debouncing, plus single-field updates. The volume is trivial
  for a single-user backend.
- **[MAG is staging]** Its URL will change when production MAG arrives. → It is one preset
  value, and nothing about MAG's URL is stored per researcher.
- **[OLCF's default is unproven]** `gpt-oss-120b` is OLCF's documented example, but whether
  it handles VISTA's tool calls on OLCF's vLLM has not been tried with a real token. → It
  is one preset value; the picker still lists and accepts any other OLCF model.
- **[A second S3M token]** A researcher with an Odo or Frontier token enters another for
  OLCF Inference, which may turn out to be unnecessary. → Separate storage costs a paste;
  sharing one field could send a compute-only token to inference, or the reverse.
- **[Detecting "configured" by comparing with i2's preset]** A `.env` that restates i2's own
  URL and model reads as i2, not "from configuration". → That is the same target, so the
  label is still accurate.
- **[Reusing `inference_api_key` as the i2 key]** A researcher on a custom URL today shows
  as i2 with their custom key attached. → It's rare. Choosing Custom and re-entering the
  URL and key fixes it, and nothing is lost.

## Migration Plan

None beyond the automatic addition of nullable columns at startup. Existing rows read as i2
(or as "from configuration" when `.env` sets another endpoint), with their saved key as the
i2 key. Rollback is reverting the change; the new columns are ignored by older code.

## Open Questions

- MAG's default model, once access is granted and a working model is confirmed: a preset
  value only.
- The "Get a key" link for MAG: the portal-lite or production portal address, once
  confirmed.
- Whether `gpt-oss-120b` handles tool calls on OLCF Inference, to confirm or change its
  default.
- Whether one S3M token can serve both compute and the Inference Service, which would let
  the OLCF key field offer the cluster token.
