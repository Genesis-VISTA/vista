## Context

See `proposal.md` - Why. Relevant existing state, all on this branch already:

- `backend/src/vista_backend/agents/inference.py` resolves which model/endpoint/credential a
  given user's agent calls use (`resolve_inference_target`, `build_inference_model`,
  `build_model_for`), with precedence: the user's own `inference_model` /
  `inference_base_url` / `inference_api_key` row, then `Settings` (env/`.env`). This is called
  per agent build (`agents.py:382`, `:465`) - already per-request in effect, since a fresh
  `Model` is built each time.
- `_CONFIGURED_ENDPOINT_PROVIDERS = {"openai", "openai-chat", "openai-responses"}` in the same
  file is the set of provider prefixes `inference.py` pins to the configured
  `openai_base_url`/`openai_api_key` rather than letting pydantic-ai fall through to its own
  environment lookup. Only endpoints reached this way are guaranteed OpenAI-compatible, and
  therefore guaranteed to expose `GET /v1/models`.
- `MissingInferenceCredential` already carries the researcher-facing message ("no inference
  credential is configured... add one in Settings...") used when a chat turn is attempted
  without a credential. The picker should report the same condition the same way, not invent a
  second wording.
- `UserSettingsModal.tsx` already reads/writes `inference_model` via the existing user-update
  path; the picker is an additional writer of the same field, not a new field.

## Goals / Non-Goals

**Goals:**
- Let a researcher discover and pick a model from the chat window, backed by the endpoint's own
  live model list rather than a hand-maintained catalog.
- Reuse `resolve_inference_target` and the existing `inference_model` field as the single source
  of truth for "which model a researcher is using" - the picker does not introduce a second,
  parallel notion of "current model."

**Non-Goals:**
- Per-turn or per-message model override. `AgentRunRequest` is unchanged; a model change takes
  effect on the researcher's next turn, the same way editing the settings-modal field already
  does today.
- Discovery for endpoints outside `_CONFIGURED_ENDPOINT_PROVIDERS`. Azure and any other provider
  keep the existing free-text field; this change does not attempt to normalize discovery across
  provider shapes.
- Any change to `resolve_inference_target`'s precedence rules, or to how credentials are stored.

## Decisions

**A thin backend proxy, not a client-side call to the gateway.** The frontend never gets the
researcher's `inference_api_key` directly (it's `SecretStr` server-side and never round-trips to
the client as plaintext), so listing models has to happen server-side, reusing
`resolve_inference_target(user)` the same way a chat turn does. Alternative considered: cache the
gateway's model list globally (one fetch for the whole deployment) rather than per-user - rejected
because a researcher's `inference_base_url` can differ from the deployment default (it's a
per-user override), so a global cache could show models from the wrong endpoint.

**Scope discovery to `_CONFIGURED_ENDPOINT_PROVIDERS`, using the same set `inference.py` already
defines.** Reusing that existing partition (rather than inventing a new "does this provider
support listing" check) keeps "is this endpoint one we control the base URL/credential for" and
"is this endpoint one we can list models from" the same question, since today they coincide (only
the OpenAI-compatible path is pinned to a known, configured base URL). If a future provider needs
its own listing logic, `_CONFIGURED_ENDPOINT_PROVIDERS` is the seam to extend, not a new parallel
list.

**No new "current model" endpoint.** The picker reads the same user object the settings modal
already fetches (`GET /users/me`) rather than adding a dedicated "what's my active model"
endpoint - the field already exists and is already fetched on load.

**Selection writes through the existing `PUT /users/me`.** No new mutation endpoint; the picker
constructs the same partial update the settings modal's `inference_model` field already sends.
This also means the two surfaces can't drift in how they persist a selection.

## Risks / Trade-offs

- **A model that disappears from the endpoint between listing and use.** The picker shows a
  snapshot; if a model is retired between when the list was fetched and when the researcher's
  next turn runs, the chat turn fails at the provider. Mitigation: this is an existing failure
  mode today (a stale hand-typed string has the same problem) - out of scope to solve here, but
  worth surfacing the provider's error clearly when it happens (existing chat-error handling,
  not new work).
- **Listing latency on open.** A live call to the gateway on every picker open adds a round trip
  the free-text field never had. Mitigation: cache the result for the session client-side (fetch
  once per mount, like `useProjects`), not on every keystroke or every open.
- **Raw model IDs are unlabeled.** `GET /v1/models` returns bare `id`/`owned_by`, no display name
  or capability metadata, so grouping/labeling in the UI is a client-side heuristic, not
  provider-sourced fact. Mitigation: keep any such labeling visually distinct from the literal ID
  (which is what's actually sent to the endpoint), so a wrong guess about "fast" vs "high
  reasoning" can't be mistaken for the endpoint's own claim.

## Migration Plan

No data migration. No feature flag needed: the endpoint and dropdown are new surfaces that fail
closed (empty list / disabled state) for a researcher with no inference credential configured, so
there's no behavior change for anyone until they open the picker.
