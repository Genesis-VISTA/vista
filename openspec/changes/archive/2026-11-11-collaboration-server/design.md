## Context

The decisions are fixed: the local backend is the source of truth, collaboration is
explicit publish and import, versions are numbered by the server with a compare-and-set on
`parent_version`, and auth is a stub until deployment is decided. The backend already has an
`install_id` and `display_name` (`single-user-backend`).

## Decisions

### D1: The server knows nothing of what it stores

Items carry a `kind` (`skill`, `project`, `knowledge-base`, any lowercase slug) and versions a
JSON `manifest` and an optional blob. The server validates their shape, never their content,
so Steps 4–6 add kinds without touching it. The plan's column list is kept, plus
`created_by_name` on both tables: the display-name header otherwise has no use, and Step 4's
listings want a name to show.

The tables derive from `CollabTable`, a SQLModel base on its own `registry()`, so they are on
their own `MetaData`. The backend's tests import this package into the backend's process; on
the shared `SQLModel.metadata` each side's `create_all` would make the other's tables, and a
backend table named `item` would collide.

### D1a: An item is created with its version 1

`POST /items {kind, name, manifest, blob_ref}` inserts the item and its version 1 in one
transaction, so `head_version` is always an integer and there is no version-less item for a
failed or cancelled second call to leave behind, nor for a listing to explain. Publishing new is
one round trip; `parent_version` on later publishes is a required integer ≥ 1.

### D2: Compare-and-set is one conditional `UPDATE`

`UPDATE item SET head_version = :parent + 1 WHERE id = :id AND head_version = :parent`, then the
version row, then commit. The `UPDATE` is the transaction's first statement, so on SQLite it
takes the write lock before anything is read: a concurrent publisher waits (busy timeout) and
then matches no row. No read precedes it, because a read would open a WAL snapshot that
cannot then be upgraded to a write once another commit lands. Zero rows matched means `404`
if the item is missing, else `409` with the head. `parent_version` is required, so leaving it
out cannot publish over an unseen head.

### D3: Blobs are uploaded first, content-addressed

`POST /blobs` (multipart) stores the bytes as `blobs/<2 hex>/<sha256>` and returns the hash;
a publish names it as `blob_ref` and is refused (`422`) if it is not stored. This keeps the
compare-and-set request small and JSON, makes stored blobs immutable by construction, and
leaves room for chunked uploads (Step 6) without changing the publish. A publish that loses
the compare-and-set leaves its blob unreferenced; a retry usually uploads the same bytes and
reuses it. No garbage collection yet.

### D4: Identity in one dependency

`services/identity.get_identity` reads `X-Vista-Install-Id` (a UUID, else `400`) and
`X-Vista-Display-Name` (percent-encoded UTF-8, since header values are Latin-1 at best), and
trusts them. Writes require it (`401` without); reads take no identity until access control
is decided, which then lands in this module.

### D5: An app factory, mounted in-process by backend tests

`create_app(settings)` keeps each app's engine and settings on `app.state`, so a test gives it
a temporary data directory. Backend tests set the client module's `transport` to
`httpx.ASGITransport(app)` and enter `app.router.lifespan_context` themselves (the transport
runs no lifespan). The server is a path dependency in the backend's `dev` **extra**, not its
`dev` group: the package build runs `uv sync --frozen` with default groups, and the server is
not staged into a package. CI's backend test job therefore syncs `--extra dev`.

### D6: The address is a setting with a configured fallback

`collaboration_server_url` lives in the settings row. It and the configured fallback share one
type, `utils.types.HttpUrl`: parsed by `httpx.URL` (what will send to it, and stricter than
`urlparse`, which accepts `http://host:abc`), `http` or `https` with a host, trailing slash
dropped. A malformed configured value stops the backend at startup; a blank one (an empty
`.env` line) means unset. Since httpx parses the address the same way when the client is built,
no stored or configured address can fail there, and the client does not guard for it. When unset, `Settings.collaboration_server_url`
(`VISTA_BACKEND_COLLABORATION_SERVER_URL`) applies, which is how `./launch.sh --collab` points
a dev backend at its server without writing to the researcher's settings. The status says
which (`source`).

### D7: Status is checked live, briefly, and never raises

`GET /collaboration/status` asks the server's `/version` with a 5 s timeout on every call; a
connection error, timeout, error status, non-VISTA answer or other API version each becomes
`reachable: false` with a sentence naming the address. The backend speaks API version 1 and
refuses any other by name. The UI store (`lib/collaboration.ts`) asks on first use, again for
a subscriber arriving after 30 s, and whenever the address is saved; `available` is false until
an answer arrives, so a control is never enabled for a reason the researcher was not shown.

### D8: One error path in the client

Every client method goes through `_request` (transport errors → `CollaborationUnavailable`,
`409` with a head → `VersionConflict`, any other error status → `CollaborationRefused` with
FastAPI's validation messages joined) and `_call` (an answer that does not parse →
`CollaborationError`; adapters are cached per shape). `download_blob` streams but shares
`_check`, and a local file that cannot be read or written is a `CollaborationError` too. So a
caller handles `CollaborationError` and nothing else.

Redirects are not followed. On a 301 or 302 httpx resends a `POST` as a `GET` without its body,
so following them would show a moved server as connected while every publish failed, and would
send the identity headers to wherever the redirect points. `_check` turns any redirect into
`CollaborationUnavailable` naming the new address, so the status says to use it.

## Deferred to Step 6

- The blob size limit protects the store, not the disk: Starlette spools the whole multipart
  body before `blobs.store` copies it, and both the server's copy and the client's
  `upload_blob` read do file I/O on the event loop. Fine for skill archives on loopback;
  knowledge bases get a streamed, chunked upload. That upload should also refuse an oversized
  `Content-Length` before parsing anything: with no authentication yet, any caller that can
  reach the server can make it spool a body to disk.

## Risks

- No authentication: anyone who can reach the server can read and write everything. It binds
  to loopback by default and logs a warning on any other address.
- Unreferenced blobs accumulate from conflicted publishes. Bounded by what researchers upload;
  collection comes with deletion.
- `uv lock` for the backend also rewrote some unrelated CUDA markers, as the local uv version
  writes them.
