# The collaboration server

The collaboration server (`collaboration_server/`) is a FastAPI service where researchers
explicitly publish, and explicitly import, VISTA projects, skills and knowledge bases. It is
optional: a researcher points VISTA at one under Settings → Collaboration, and without one,
publishing and importing are off. No VISTA package starts one.

It stores *items* of any kind, each created with its first version and given more, numbered
2, 3… by the server, none ever changed; what a version holds (a JSON manifest and optionally
one blob) is for the VISTA backends to read. Its database is SQLite and its blobs are files
named by their SHA-256, both under its data directory.

## Run

```bash
./launch.sh logs --collab
```

starts a local server in `dev-headers` mode beside the other services and points the backend at
it ([Developing VISTA](development.md#with-a-collaboration-server)). To run one on its own:

```bash
cd collaboration_server
uv run vista-collaboration-server
```

It listens on `127.0.0.1:8002` and keeps its data in `../data/collaboration-server`; settings
use the `VISTA_COLLAB_` prefix (see
[`config.py`](../collaboration_server/src/vista_collaboration_server/config.py)). Run bare like
this it is in `oidc` mode, so it needs a realm to check tokens against (see
[Deploying](#deploying)).

## Test

```bash
cd collaboration_server
uv run --extra dev pytest -vv
```

or `./scripts/ci-local.sh collab` from the repository root, which also lints and type-checks.

## Authentication

[`services/identity.py`](../collaboration_server/src/vista_collaboration_server/services/identity.py)
is the one place it lives, and every route but `GET /version` needs a user from it. `auth_mode`
picks how:

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
`Authorization: Bearer <access token>`, such as APISIX's `openid-connect` plugin or
oauth2-proxy. Only `GET /sign-in` needs the gateway's browser login (a login page when there is
no session); every other route takes the token alone, as VISTA sends it.

`GET /sign-in` shows the signed-in researcher the token the gateway forwarded, after checking it
like any request's (never cached or framed, and running no script). In VISTA, Settings →
Collaboration links to `<server>/sign-in`, and the researcher pastes the token into a write-only
field, like a repository token. The backend sends it only over https (or to loopback), forgets
it when the server's address changes, and, when the server refuses it, says to get a new one
from that page. A `dev-headers` server needs no token.

A token from `/sign-in` cannot be renewed: researchers fetch a new one when it expires, after
the realm's access token lifespan.

Writes record who made them (`created_by` is the token's `sub`, with `created_by_name`, and the
install id the request claimed as `created_by_install`; `created_by_email` is kept but never
served), and `GET /me` says who the server takes the caller to be, and their teams.

## Teams

Every item belongs to one team, and only the team's members can see it or publish to it
([`services/teams.py`](../collaboration_server/src/vista_collaboration_server/services/teams.py)).
Everyone is in the **public** team (id `00000000-0000-4000-8000-000000000000` on every server),
which has no member rows and starts empty; it holds anything published for everyone, and anyone
signed in can publish a new version of an item in it. The server holds no seeds: VISTA's seeded
projects and knowledge bases, and its bundled skills, are private on each installation until
published to a team.

- Anyone signed in can create a team and is its first member. Names are unique in any case.
- Any member can add anyone who has signed in to the server (`GET /users` finds them by name
  or email; the server records each caller as they sign in, in `services/users.py`). Everyone
  signed in can see everyone's name and email there.
- Members can leave, except the last: a team nobody is in would hold items nobody could reach.
- An item or team the caller is not in is `404`, the same answer as one that does not exist.
  Blob uploads are open to anyone signed in; a blob is read only through a version they can see,
  and a version may name only a blob its publisher uploaded or sees in another version
  (`services/blob_access.py`), since knowing a blob's SHA-256 is no proof of having it.

There are no team roles yet: every member can add people. Owners who alone can, or remove
others, may follow.

## Copying

`POST /items/{id}/copy` (`{team_id, name?}`) makes a new item in another of the caller's teams
whose version 1 is the original's newest version, with the same manifest and blob, so nothing
is uploaded again; `copied_from` names the original, which stays as it is. Items are copied
between teams, never moved. VISTA does not use it: in VISTA an item stays in the team it was
first published to, and a private project, skill or knowledge base is published into a team as
a new item.

The server chooses every item's id; a client cannot.

## API

| Route | |
|---|---|
| `GET /version` | `server_version` and `api_version`; needs no user |
| `GET /me` | the caller: `{sub, email, name, teams}` |
| `GET /sign-in` | an HTML page showing the caller's token, for pasting into VISTA (through a login gateway) |
| `GET /items?kind=&team_id=` | the caller's teams' items, oldest first |
| `POST /items` | `{kind, name, team_id, manifest, blob_ref}`: a new item in one of the caller's teams, and its version 1 |
| `POST /items/{id}/copy` | `{team_id, name?}`: a new item in another of the caller's teams from its newest version |
| `GET /items/{id}` | one item, with its `head_version` |
| `GET /items/{id}/versions` | its versions |
| `POST /items/{id}/versions` | `{parent_version, manifest, blob_ref}`: the next version, accepted only if `parent_version` is still the head; otherwise `409` with `head_version` |
| `GET /items/{id}/versions/{n}` | one version |
| `GET /items/{id}/versions/{n}/blob` | its blob |
| `POST /blobs` | multipart `file`: store a blob, returning `blob_ref` |
| `POST /blobs/stream` | the raw body: store a blob too large for a multipart request, returning `blob_ref` |
| `GET /teams` | the caller's teams, the public team first |
| `POST /teams` | `{name}`: a new team, with the caller as its member |
| `GET /teams/{id}` | one of the caller's teams |
| `GET /teams/{id}/members` | its members: `{sub, email, name, added_by, added_at}` |
| `POST /teams/{id}/members` | `{sub}`: add someone who has signed in |
| `DELETE /teams/{id}/members/me` | leave it; `409` for its last member |
| `GET /users?q=` | everyone signed in whose name or email holds `q` |

Every route but `/version` needs a user, reads included. `max_blob_size` caps a blob either way. A
streamed blob is written to disk as it arrives, never held whole.

The server reads neither manifests nor blobs. What VISTA keeps in them, per kind, is the
backend's business: a `skill` version's blob is the skill's folder as a gzipped tar and its
manifest is read from the SKILL.md
([`skill_collaboration.py`](../backend/src/vista_backend/services/skill_collaboration.py)); a
`project` version has no blob, and its manifest holds the project's fields and references to
the skill and knowledge base versions it uses
([`project_collaboration.py`](../backend/src/vista_backend/services/project_collaboration.py));
a `knowledge-base` version's blob is a tar of its PDFs and built index, streamed up, and its
manifest says what built the index
([`knowledge_base_collaboration.py`](../backend/src/vista_backend/services/knowledge_base_collaboration.py)).

## Deploying

A deployment runs in `oidc` mode behind a login gateway, with the realm set in
`VISTA_COLLAB_OIDC_ISSUER`, the clients whose tokens it accepts in `VISTA_COLLAB_OIDC_ALLOWED_AZP`,
and the client role a researcher needs in `VISTA_COLLAB_OIDC_REQUIRED_ROLE`. Who can sign in is
therefore decided in the realm, by who holds that role. When the gateway serves the server under
a path prefix and strips it, `VISTA_COLLAB_ROOT_PATH` puts the prefix back in the API docs'
links. On NFS, where SQLite's WAL does not work, set `VISTA_COLLAB_SQLITE_JOURNAL_MODE=DELETE`.

[`collaboration_server/deploy/`](../collaboration_server/deploy/) holds VISTA's own deployment,
a systemd unit and its environment, as an example.
