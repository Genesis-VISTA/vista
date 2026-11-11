## Why

Researchers want to share projects, skills and knowledge bases between VISTA installations,
and to decide for themselves what leaves their computer. The agreed shape is a
separate collaboration server, explicit publish and import, server-numbered immutable
versions with a compare-and-set on the head. This is Step 3: the server and the backend's
client, with nothing published yet. Steps 4–6 build skills, projects and knowledge bases on
it. It follows `single-user-backend`, whose settings row already carries the `install_id` and
`display_name` this identifies itself with.

## What Changes

- New uv project `collaboration_server/` (`vista_collaboration_server`, FastAPI, SQLModel on
  SQLite, blobs under its own data directory), started with `vista-collaboration-server` on
  `127.0.0.1:8002` and configured by `VISTA_COLLAB_*`.
- A generic model: `item` (`id`, `kind`, `name`, `created_by`, `head_version`, `created_at`)
  and immutable `item_version` (`item_id`, `version`, `parent_version`, `manifest`,
  `blob_ref`, `created_by`, `created_at`). `POST /items` creates an item with its version 1;
  `POST /items/{id}/versions` accepts only a `parent_version` equal to the head, in one
  transaction, else `409` with the head. Blobs are uploaded first (`POST /blobs`) and named by
  their SHA-256. The tables are on the server's own SQLModel registry.
- An identity stub: the `X-Vista-Install-Id` and `X-Vista-Display-Name` headers, trusted, in
  one dependency where authentication plugs in later. `GET /version` returns the server and
  API versions.
- Backend: `collaboration_server_url` in the settings row (falling back to
  `VISTA_BACKEND_COLLABORATION_SERVER_URL`), `services/collaboration.py` (a client for the
  item/version API), and `GET /collaboration/status` → `{configured, reachable, reason, url,
  source, server_version}`. An unreachable server is never fatal.
- UI: a Collaboration section in Settings (address and status), a proxy route, and
  `useCollaboration()`, the hook Publish and Import controls will read to be disabled with
  the reason.
- Dev and test: `./launch.sh --collab`; the backend's collaboration tests mount the server
  in-process through its dev extra; a `collab` target in `scripts/ci-local.sh` and
  `collab:lint`, `collab:typecheck`, `collab:test` in `.gitlab-ci.yml`.

## Capabilities

### New Capabilities

- `collaboration-server`: the server's item/version API, compare-and-set publish, blobs,
  identity stub and defaults.
- `collaboration-client`: which server a backend uses, its status, and how the interface
  shows it and gates publish and import on it.

### Modified Capabilities

- `testing-ci`: the local CI mirror gains the `collab` target.

## Non-goals

- Publishing or importing anything: skills (Step 4), projects (Step 5) and knowledge bases
  (Step 6) are later changes; no Publish or Import control exists yet.
- Authentication or access control on the server; deployment of it.
- Starting a collaboration server from a package, package launcher or the Electron startup
  protocol.
- Chunked or resumable uploads, deleting items, and garbage-collecting unreferenced blobs.
- VISTAGuard.
