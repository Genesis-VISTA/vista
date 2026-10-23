# vista-collaboration-server

FastAPI service where researchers explicitly publish, and explicitly import, VISTA projects,
skills and knowledge bases. It stores *items* of any kind, each created with its first version
and given more, numbered 2, 3… by the server, none ever changed; what a version holds (a JSON
manifest and optionally one blob) is for the VISTA backends to read. Its database is SQLite and
its blobs are files named by their SHA-256, both under its data directory.

There is no authentication yet: a backend names itself with the `X-Vista-Install-Id` and
`X-Vista-Display-Name` headers and is believed
([`services/identity.py`](src/vista_collaboration_server/services/identity.py)), so anyone who
can reach the server can read and write everything on it. It listens on `127.0.0.1` by default
for that reason.

## Run

```bash
cd collaboration_server
uv run vista-collaboration-server
```

It listens on `127.0.0.1:8002` and keeps its data in `../data/collaboration-server`; settings
use the `VISTA_COLLAB_` prefix (see [`config.py`](src/vista_collaboration_server/config.py)).
`./launch.sh --collab` starts it beside the other services and points the backend at it. It is
for development and tests: no VISTA package starts one.

## API

| Route | |
|---|---|
| `GET /version` | `server_version` and `api_version` |
| `GET /items?kind=` | items, oldest first |
| `POST /items` | `{kind, name, manifest, blob_ref}`: a new item and its version 1 |
| `GET /items/{id}` | one item, with its `head_version` |
| `GET /items/{id}/versions` | its versions |
| `POST /items/{id}/versions` | `{parent_version, manifest, blob_ref}`: the next version, accepted only if `parent_version` is still the head; otherwise `409` with `head_version` |
| `GET /items/{id}/versions/{n}` | one version |
| `GET /items/{id}/versions/{n}/blob` | its blob |
| `POST /blobs` | multipart `file`: store a blob, returning `blob_ref` |

Writes need `X-Vista-Install-Id`; reads do not.

The server reads neither manifests nor blobs. What VISTA keeps in them, per kind, is the
backend's business: a `skill` version's blob is the skill's folder as a gzipped tar and its
manifest is read from the SKILL.md (`backend/src/vista_backend/services/skill_collaboration.py`);
a `project` version has no blob, and its manifest holds the project's fields and references
to the skill versions it loads (`backend/src/vista_backend/services/project_collaboration.py`).

## Test

```bash
cd collaboration_server
uv run --extra dev pytest -vv
```

or `./scripts/ci-local.sh collab` from the repository root, which also lints and type-checks.
