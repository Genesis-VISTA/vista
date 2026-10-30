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

## Deployment

The collaboration server is currently deployed on Holly behind AAIMS group Keycloak auth.

To set up a deployment from scratch:
```bash
sudo useradd --system --uid 910 --user-group --home-dir /sw/holly/apps/vista/var --no-create-home --shell /usr/sbin/nologin vista
sudo usermod -aG vista $USER
# logout/login to refresh groups
mkdir -p /sw/holly/apps/vista
chown -R $USER:vista /sw/holly/apps/vista
chmod -R u+rwX,g+rwX,o-rwx /sw/holly/apps/vista

# Start session as vista user
sudo su -s /bin/bash - vista

cd /sw/holly/apps/vista
mkdir -p bin etc var
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$PWD/bin" UV_NO_MODIFY_PATH=1 sh

git clone https://github.com/Genesis-VISTA/vista.git vista
# or
# read -s GITLAB_TOKEN # Generate a readonly gitlab token to pull VISTA, paste it here
# echo "https://oauth:$GITLAB_TOKEN@gitlab.com" > /$HOME/.git-credentials
# chmod 600 /$HOME/.git-credentials
# git config --global credential.helper store
# git clone https://gitlab.com/amsc2/genesis/vista.git ./vista

cp vista/collaboration_server/deploy/holly/env.example etc/env

# edit etc/env if needed (defaults should be fine though)
chmod -R u+rwX,g+rwX,o-rwx *

# exit vista user shell

cat /sw/holly/apps/vista/vista/collaboration_server/deploy/holly/vista-collab.service | sudo tee /etc/systemd/system/vista-collab.service
sudo systemctl daemon-reload && sudo systemctl enable --now vista-collab

# view logs during boot
sudo journalctl -u vista-collab -n 50 -f
# verify
no_proxy='localhost,127.0.0.1,0.0.0.0' curl  -k https://127.0.0.1:8081/version
```

To update existing installation:
```bash
# Start session as vista user
sudo su -s /bin/bash - vista

cd /sw/holly/apps/vista
git -C vista pull

# update env if needed
cp vista/collaboration_server/deploy/holly/env.example etc/env

chmod -R u+rwX,g+rwX,o-rwx *

# exit vista user shell

cat /sw/holly/apps/vista/vista/collaboration_server/deploy/holly/vista-collab.service | sudo tee /etc/systemd/system/vista-collab.service
sudo systemctl daemon-reload
sudo systemctl restart vista-collab
```

Currently, vista is not part of the Holly ansible set up, and so the service won't persist on reboot. To start it manually:
```bash
cat /sw/holly/apps/vista/vista/collaboration_server/deploy/holly/vista-collab.service | sudo tee /etc/systemd/system/vista-collab.service
sudo systemctl daemon-reload && sudo systemctl enable --now vista-collab
```

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
