# vista-collaboration-server

FastAPI service where researchers explicitly publish, and explicitly import, VISTA projects,
skills and knowledge bases. It stores *items* of any kind, each created with its first version
and given more, numbered 2, 3… by the server, none ever changed; what a version holds (a JSON
manifest and optionally one blob) is for the VISTA backends to read. Its database is SQLite and
its blobs are files named by their SHA-256, both under its data directory.

## Authentication

[`services/identity.py`](src/vista_collaboration_server/services/identity.py) is the one place
it lives, and every route but `GET /version` needs a user from it. `auth_mode` picks how:

- **`oidc`** (the default, and what a deployment runs): an access token from the configured
  OIDC realm (`oidc_issuer`) as `Authorization: Bearer`. The server checks it itself: its
  signature against the realm's published keys (fetched from its JWKS endpoint by PyJWT's
  `PyJWKClient`; when they cannot be fetched the answer is 503, not a refused token), `iss`,
  `exp`, `typ`, `azp` (one of `oidc_allowed_azp`), and
  the `oidc_required_role` client role. A gateway in front may check it too; nothing relies on
  that.
- **`dev-headers`**: the `X-Vista-Install-Id` and `X-Vista-Display-Name` headers, believed as
  sent, for `./launch.sh --collab` and tests. The server refuses to start with it on anything
  but loopback.

### Signing in: the `/sign-in` page

The server issues no tokens and runs no login. A **login gateway** in front of it does: any
reverse proxy that logs a browser in with the realm and forwards the request with
`Authorization: Bearer <access token>`. On VISTA's deployment that is Obsidian's APISIX
(`openid-connect`); a self-hosted server could use oauth2-proxy, or anything else that
forwards the token. Only `GET /sign-in` needs the gateway's browser login (a login page when
there is no session); every other route takes the token alone, as VISTA sends it.

`GET /sign-in` shows the signed-in researcher the token the gateway forwarded, after checking it
like any request's (never cached or framed, and running no script). In VISTA, Settings →
Collaboration links to `<server>/sign-in`, and the researcher pastes the token into a write-only
field, like a repository token. The backend sends it only over https (or to loopback), forgets
it when the server's address changes, and, when the server refuses it, says to get a new one
from that page. A `dev-headers` server needs no token.

Every signed-in user may read and write everything for now. Writes record who made them
(`created_by` is the token's `sub`, with `created_by_name`, and the install id the request
claimed as `created_by_install`; `created_by_email` is kept but never served), and `GET /me` says
who the server takes the caller to be, so access control can be decided from `User.sub` later.

## Run

```bash
cd collaboration_server
uv run vista-collaboration-server
```

It listens on `127.0.0.1:8002` and keeps its data in `../data/collaboration-server`; settings
use the `VISTA_COLLAB_` prefix (see [`config.py`](src/vista_collaboration_server/config.py)).
Run bare like this it is in `oidc` mode; `./launch.sh --collab` starts it in `dev-headers`
mode beside the other services and points the backend at it. No VISTA package starts one.

## API

| Route | |
|---|---|
| `GET /version` | `server_version` and `api_version`; needs no user |
| `GET /me` | the caller: `{sub, email, name}` |
| `GET /sign-in` | an HTML page showing the caller's token, for pasting into VISTA (through a login gateway) |
| `GET /items?kind=` | items, oldest first |
| `POST /items` | `{kind, name, manifest, blob_ref}`: a new item and its version 1 |
| `GET /items/{id}` | one item, with its `head_version` |
| `GET /items/{id}/versions` | its versions |
| `POST /items/{id}/versions` | `{parent_version, manifest, blob_ref}`: the next version, accepted only if `parent_version` is still the head; otherwise `409` with `head_version` |
| `GET /items/{id}/versions/{n}` | one version |
| `GET /items/{id}/versions/{n}/blob` | its blob |
| `POST /blobs` | multipart `file`: store a blob, returning `blob_ref` |
| `POST /blobs/stream` | the raw body: store a blob too large for a multipart request, returning `blob_ref` |

Every route but `/version` needs a user, reads included. `max_blob_size` caps a blob either way. A
streamed blob is written to disk as it arrives, never held whole.

The server reads neither manifests nor blobs. What VISTA keeps in them, per kind, is the
backend's business: a `skill` version's blob is the skill's folder as a gzipped tar and its
manifest is read from the SKILL.md (`backend/src/vista_backend/services/skill_collaboration.py`);
a `project` version has no blob, and its manifest holds the project's fields and references
to the skill and knowledge base versions it uses
(`backend/src/vista_backend/services/project_collaboration.py`); a `knowledge-base` version's
blob is a tar of its PDFs and built index, streamed up, and its manifest says what
built the index (`backend/src/vista_backend/services/knowledge_base_collaboration.py`).

## Test

```bash
cd collaboration_server
uv run --extra dev pytest -vv
```

or `./scripts/ci-local.sh collab` from the repository root, which also lints and type-checks.

## Deployment: Holly, behind Obsidian

```
VISTA Desktop (its backend, on the researcher's machine)
  → https://obsidian.ccs.ornl.gov/vista/api/*   APISIX: openid-connect + authz-keycloak (apisix/vista),
                                                strips /vista/api; /vista/api/sign-in is a browser
                                                login, the rest token-only
  → https://172.30.26.90:8001                   Holly: self-signed TLS, not verified by APISIX
  → vista-collaboration-server                  auth_mode=oidc, root_path=/vista/api
```

The gateway's half is in the Obsidian repository, `infra/apisix/config.jsonnet` (the
`vista-api` service and its three `/vista/api` routes), and the `apisix/vista` role and the
`VISTA` resource and policy in `infra/kc/obsidian-realm.json`. Beta users are given the role in
Keycloak's admin console.

Holly's image is built reproducibly, so everything lives on the `/sw/holly/apps` NFS mount,
under `/sw/holly/apps/vista`, and the image gains only the systemd unit in
`/etc/systemd/system`. That is a copy, not a link: systemd reads its units at boot, before NFS
is mounted. The server runs as an account with a fixed uid that owns
`/sw/holly/apps/vista` (`User=` in the unit; `vista` there). The database is on NFS, so it uses
SQLite's rollback journal (`VISTA_COLLAB_SQLITE_JOURNAL_MODE=DELETE`): WAL needs shared memory,
which a network filesystem cannot give.

The directory stays closed to everyone but that account and its group (mode 770, `etc/env`
660), so systemd, running as root and squashed to nobody by NFS, cannot open anything in it.
The unit is written for that: `launch.sh` reads `etc/env` itself, as the service's user, and
there is no `EnvironmentFile`, `WorkingDirectory` or `ProtectSystem` sandbox, each of which
systemd would resolve as root. Never make `etc/env` world-readable to get around this.

As that account (NFS's `root_squash` usually stops root writing there):

```bash
cd /sw/holly/apps/vista
mkdir -p bin etc var
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$PWD/bin" UV_NO_MODIFY_PATH=1 sh
git clone <the VISTA repository> vista
cp vista/collaboration_server/deploy/holly/env.example etc/env     # then review it
```

Then, as root:

```bash
sudo cp /sw/holly/apps/vista/vista/collaboration_server/deploy/holly/vista-collab.service \
  /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now vista-collab
journalctl -u vista-collab -f
```

[`deploy/holly/launch.sh`](deploy/holly/launch.sh) makes a ten-year self-signed certificate on
first start if there is none, then runs the server. To update, pull the checkout and
`sudo systemctl restart vista-collab`; copy the unit again only when it changes. Then check
it end to end, through the gateway, with a real account (manual only; it leaves a `smoke-test` item behind):

```bash
uv run python scripts/smoke.py https://obsidian.ccs.ornl.gov/vista/api
```

The self-signed certificate keeps tokens and data unreadable on the network between Slate and
Holly. Because APISIX does not verify it, it does not stop someone able to pose as Holly on
that network; such a person could capture a token and use it until it expires, but not forge
one.

### Follow-ups

- **Renewable sign-ins.** A token from `/sign-in` cannot be renewed: researchers fetch a new
  one when it expires (at most the realm's 12-hour access token lifespan). VISTA could run its
  own OAuth flow on Keycloak's `obsidian-public` client instead, the device grant (a link and a
  short code to approve) or authorization code + PKCE, and with `offline_access` renew its token
  for weeks. That needs the flow enabled on the client, and `obsidian-public` added to
  `oidc_allowed_azp`.
- **Pin Holly's certificate in APISIX**, so the gateway verifies it is talking to Holly. APISIX
  (3.17) has no per-upstream switch for this (`upstream.tls.verify` is Kafka-only) and never
  sets nginx's `proxy_ssl_verify`, which is off by default: it would take `proxy_ssl_verify on`
  and `proxy_ssl_trusted_certificate` in Obsidian's `http_server_configuration_snippet`, which
  applies to every HTTPS upstream, and a certificate with `IP:172.30.26.90` as a subject
  alternative name, since APISIX connects by address.
- **Access control**: who may publish new versions of an item, decided from `User.sub`.
