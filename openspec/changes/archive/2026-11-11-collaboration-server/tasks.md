## 1. Collaboration server

- [x] 1.1 Add the uv project `collaboration_server/` (`vista_collaboration_server`): `CollabSettings` (`VISTA_COLLAB_*`, `127.0.0.1:8002`, data dir, SQLite URL, blob limit), the `vista-collaboration-server` entry point, and `create_app(settings)`
- [x] 1.2 Add `item` and `item_version` (`db/schemas.py`) on the server's own SQLModel registry, and the engine with WAL and a busy timeout (`db/db.py`)
- [x] 1.3 Add the identity stub (`services/identity.py`): trusted `X-Vista-Install-Id` / `X-Vista-Display-Name`, required on writes
- [x] 1.4 Add the item routes (an item is created with its version 1), the compare-and-set publish (`services/items.py`), the content-addressed blob store (`services/blobs.py`) and `GET /version`
- [x] 1.5 Tests in `collaboration_server/tests/test_items.py`: numbering, `409` with the head, concurrent publishes, immutability, blobs, identity, restart

## 2. Backend

- [x] 2.1 Add `collaboration_server_url` to the settings row, update and public models, and the `Settings.collaboration_server_url` fallback, both validated by `utils.types.HttpUrl`
- [x] 2.2 Add `services/collaboration.py` (server in effect, client for the item/version API raising only `CollaborationError`, `status`) and `GET /collaboration/status`
- [x] 2.3 Add the server as a path dependency in the backend's `dev` extra, and the in-process `collab_server` fixture (`tests/conftest.py`)
- [x] 2.4 Tests in `backend/tests/test_collaboration.py`: not configured, fallback, unreachable, timeout, not a VISTA server, other API version, the route, and a client round trip against the real server

## 3. UI

- [x] 3.1 Add `collaboration_server_url` to `lib/settings.ts` and the proxy `app/api/collaboration/status/route.ts`
- [x] 3.2 Add `lib/collaboration.ts` (`useCollaboration`, `availability`, `refreshCollaborationStatus`) with tests in `tests/collaboration.test.ts`
- [x] 3.3 Add the Collaboration section to the settings modal, with tests in `tests/UserSettingsModal.test.tsx`, and the status to the hermetic fixtures

## 4. Dev, CI and docs

- [x] 4.1 `./launch.sh --collab` starts the server and sets `VISTA_BACKEND_COLLABORATION_SERVER_URL` (not in package launchers, Electron or packaging)
- [x] 4.2 Add the `collab` target to `scripts/ci-local.sh` and `collab:lint`, `collab:typecheck`, `collab:test` to `.gitlab-ci.yml`; backend tests sync `--extra dev`
- [x] 4.3 Document it in `AGENTS.md`, `docs/development.md` and `collaboration_server/README.md`
- [x] 4.4 Run `./scripts/ci-local.sh backend collab ui docs` and `openspec validate collaboration-server`
