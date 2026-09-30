## Context

See proposal.md — Why, for motivation.

This change lands on top of `globus-under-msb`, which must archive first: it is what makes the
transfer endpoint runnable without a container runtime, and both changes modify the same two
requirements. The delta specs here are written against the text that change leaves behind.

Current state, read rather than assumed:

- `scripts/get_globus_token.py:307-324` is the flow being moved. It calls `oauth2_start_flow`,
  prints `oauth2_get_authorize_url()`, reads a code from `input()`, and exchanges it. The only
  part that does not survive the move to a web interface is `input()`.
- `globus_sdk` 4.7.0 is a dependency of `vista_mcp_server`. `oauth2_start_flow` accepts an explicit
  `verifier`, which is what lets a PKCE flow span two HTTP requests. `TransferClient.create_endpoint`
  was removed in 4.x; `TransferClient.post` remains.
- `UserConfig` already carries per-cluster credentials in exactly the shape this needs:
  `odo_s3m_token` / `frontier_s3m_token` with a shared `s3m_token` fallback, resolved by
  `require_s3m_token(cluster)`.
- `schemas.py` has an encrypted `globus_token` column, all four schema variants, and
  `_USER_CONFIG_NULLABLE_FIELDS`. The value already crosses the wire in the `{"vista": {"user": …}}`
  metadata blob on every HPC tool call; `UserConfig` does not declare it, so pydantic discards it.
- `db.py`'s `_add_missing_columns` adds nullable columns at startup, so new fields need no migration.
- `gcp_vm.Endpoint.setup` already accepts a setup key and runs non-interactively when given one,
  with a test. `start`, `stop` and `status` are callable at any time.
- `vista_mcp_server/server.py` has no lifespan. `dev_mcp_server` uses `fastmcp.server.lifespan`, so
  the mechanism exists in the codebase; this server has never needed one.

## Goals / Non-Goals

**Goals:**

- One authorization per cluster, performed in the interface, with nothing to install or export.
- The deployment's environment variables keep working untouched, for the hosted server.
- The endpoint's lifetime belongs to something that is running when the credential arrives.

**Non-Goals:**

- Changing what the endpoint can see, which `globus-under-msb` settled.
- Refreshing or rotating credentials on a schedule. Transfer refresh tokens are long-lived and
  `RefreshTokenAuthorizer` renews access tokens transparently.
- A browser redirect flow. The code is pasted, as it is today.

## Decisions

### The authorization flow lives in the backend, in two calls

`start` builds the client, generates a PKCE verifier, and returns the authorization address.
`complete` takes the code, reconstructs the flow with the stored verifier, exchanges it, and stores
the refresh token on the user.

The verifier is the secret that makes PKCE worth having, so it is held server-side, keyed to the
user and short-lived, rather than round-tripped through the browser. Returning it to the client and
taking it back would leave the code sufficient on its own, which is the attack PKCE exists to stop.

The backend rather than the MCP server, because the settings modal already talks to the backend and
the credential is stored in the backend's database. `globus_sdk` becomes a backend dependency.

The alternative — a redirect URI and a callback route — removes the paste but needs a registered
redirect and a reachable URL, which a desktop install on an arbitrary port does not reliably have.
The native-app flow is what `get_globus_token.py` already uses against the same client ID, and the
paste is the step the researcher already knows.

### Per-cluster fields, mirroring the S3M pair

`odo_globus_token` and `frontier_globus_token`, with the existing `globus_token` kept as the shared
fallback, resolved by a `require_globus_token(cluster)` on `UserConfig` that mirrors
`require_s3m_token` line for line. The authorization URL pins the cluster's SSO domain through
`session_required_single_domain`, the values for which are already in
`get_globus_token.py`'s `CLUSTER_SESSION_DOMAINS`.

This follows the codebase's existing answer to the same question rather than inventing a second
one, and it means a researcher with access to one cluster is never asked to authorize against the
other's SSO.

### Resolution order: the researcher's, then the deployment's

`require_globus_token(cluster)` prefers the user's, falls back to `settings`. A hosted deployment
with no user having connected behaves exactly as it does today, which matters because Odo's
permissions model assumes a single shared identity: Globus-created directories are not
group-writable and Odo has no `setfacl`. Per-user identities change behaviour there, and the
fallback order is what contains that change to installations that opt into it.

### The collection is created from a setup key, not a terminal login

With a Transfer token in hand, creating the Globus Connect Personal endpoint through the Transfer
API returns a `globus_connect_setup_key`, which `gcp_vm.setup_command` already accepts. One
authorization in the interface then covers both the credential and the collection.

Proven end to end against real Globus before this was built on:
`POST /v0.10/endpoint` with `is_globus_connect` returns the key, `setup(key, interactive=False)`
writes a `client-id.txt` holding the created collection's own id, and after `start` Globus itself
reports `gcp_connected=True` for it. The version prefix is not optional, and `globus_sdk` 4.7 has
no `create_endpoint` helper, so the call is `TransferClient.post`.

**No extra scope is needed.** The token that did all of this was minted by
`get_globus_token.py --cluster odo`, whose scopes are the base
`transfer.api.globus.org:all` plus the identity ones. Globus Connect Personal's own setup requests
`gcp_install`, which is what led to the guess that this would too; it does not. So the interface
asks for exactly the consent the existing script asks for, and nothing about the authorization
changes to gain the collection.

The terminal login stays as the fallback and the CLI keeps `--setup`, because a headless install
still needs it.

### The MCP server owns the endpoint, and starts it on first use

`vista_mcp_server` gains a lifespan that stops the endpoint when the server stops. Starting is
lazy: the first file operation that needs a collection creates it if absent, starts the endpoint,
and waits for it, under a lock so that concurrent tool calls produce one endpoint rather than
several.

The MCP server is the only component that uses the endpoint and it already reads the collection
identifier. The credential reaches it on every HPC tool call in the metadata blob, so no new
channel between backend and MCP server is needed — which is the main reason to start lazily rather
than have the backend push a "now start" signal after `complete`.

The launcher was considered and rejected: it would have to poll for a credential owned by another
process and started after it, which is a supervisor, and this design deliberately has none.

Consequence: the first file operation after connecting pays for the microVM boot and the collection
coming online. It is reported as a step in progress rather than a hang, and it happens once per
server lifetime.

### The launcher stops starting the endpoint

The refresh-token gate, the fourth managed service and the startup line go. Startup gets shorter
and has one less thing that can half-work. `scripts/launch_globus.py` and the `python -m` entry
point stay for headless installs and for development.

## Risks / Trade-offs

- **Two enclaves, one local collection.** A transfer is authorized by a single token that must
  have rights on *both* collections, and a Globus Connect Personal installation provides one
  collection, owned by whichever identity created it. If Odo and Frontier are different identities,
  a collection created under one may not be usable by the other's token. → Carried forward
  deliberately, not solved here. `main` already works this way: two refresh tokens minted by two
  logins, one per cluster and each pinning its own SSO domain, against a single
  `vista_globus_collection_id`. This change moves where those two tokens come from and changes
  nothing about how they are used, so if the arrangement has a flaw it is a pre-existing one and
  belongs to its own change. Worth knowing when that day comes: the Odo path is exercised, and
  there is no evidence the Frontier half has ever run end to end.

- **Creating the collection through the Transfer API is unproven here.** `globus_sdk` 4.7 removed
  the helper, and the scopes that call needs are inferred: Globus Connect Personal's own setup
  requests `transfer.api.globus.org:gcp_install`, observed directly in the authorize URL it printed
  under `globus-under-msb`. → One probe settles both the call and the scope list. If it fails, the
  terminal login remains and this change still removes the environment variable, which is most of
  its value.

- **The first file operation is slow.** → Bounded, reported as progress, once per server lifetime,
  and no worse than the launcher's current cost — which is paid by every start, including the
  starts where nobody transfers anything.

- **The first researcher to transfer creates the collection under their identity.** On a desktop
  there is one researcher. On a shared deployment the collection already exists and the
  environment-variable fallback is in force, so this only arises on a fresh multi-user install. →
  Recorded rather than solved; the interface reports which identity owns the collection.

- **A single-use code pasted into a web form.** → It crosses localhost to the backend that
  generated the flow, is useless without the server-held verifier, and is spent immediately. The
  refresh token it becomes is stored in the same encrypted column the S3M tokens use.

- **`globus_token` already exists and is unused.** A field nothing reads is exactly what
  `zero-config-startup` forbids. → It becomes the shared fallback, read by
  `require_globus_token`, so it stops being decorative.

## Migration Plan

`globus-under-msb` archives first. A deployment that exports the environment variables keeps
working with no action: the fallback path is the same code the tools use today. An installation
that has already completed the terminal setup keeps its collection, since the collection lives in
the data directory and nothing here touches it — connecting in the interface then supplies only the
credential.

Rollback is reverting the commits. The environment-variable path is the one that never changes, in
either direction.

## Open Questions

None. The one unknown — whether the collection can be created through the Transfer API, and under
which scopes — is a verification step placed before the work it affects, not a deferred decision.
It does not change the specs: the researcher connects in the interface either way. What it changes
is whether creating the collection still needs a terminal the first time.

`.env.sample` already documents `globus gcp create mapped` producing a setup key, and that setup
keys are single use, which is knowledge someone acquired by using one. So the API supports this;
what the probe settles is making the same call from `globus_sdk`, whose 4.7 release removed the
`create_endpoint` helper, with scopes requested during the same login that returns the transfer
token.
