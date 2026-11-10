# The collaboration server

The collaboration server (`collaboration_server/`) is a FastAPI service where researchers
explicitly publish, and explicitly import, VISTA projects, skills and knowledge bases. It is
optional: a researcher points VISTA at one under Settings → Collaboration, and without one,
publishing and importing are off. No VISTA package starts one.

It stores *items* of any kind, each created with its first version and given more, numbered
2, 3… by the server, none ever changed; what a version holds (a JSON manifest and optionally
one blob) is for the VISTA backends to read. Its database is SQLite and its blobs are files
named by their SHA-256, both under its data directory. It can also host a project's
Hypothesis Lab forum as a git repository ([Forums](#forums)).

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

## Forums

A project's Hypothesis Lab forum can be hosted here: a git repository the server runs, per
project, that VISTA installs fetch and push over HTTP, so access follows the project's team
and the lab's rules are enforced at push time
([`hypothesis-forum-hosting.md`](hypothesis-forum-hosting.md) is the researcher's side, and
[`forum-git-format.md`](forum-git-format.md) the format). Forums are not items: a forum is live
and mutable, an item's versions never change, so they have their own tables (`forum`, and
`forum_push`, below) and routes
([`api/forums.py`](../collaboration_server/src/vista_collaboration_server/api/forums.py),
[`services/forums.py`](../collaboration_server/src/vista_collaboration_server/services/forums.py)).
The server still reads no manifests: a project item's manifest carries `{"forum": "hosted"}`
for a hosted forum, and the backend asks `GET /items/{id}/forum` where it is.

**Access.** A forum belongs to the account that created it, and is its owner's alone until it
is linked, once, to a `project` item. From then on it belongs to that item's team, and every
member can fetch and push it; the link never changes. A forum the caller may not use, or that
does not exist, is `404`, as for items.

**Git.** The server requires **git 2.34 or later** on its host, and only for forums: it checks
at startup, never running macOS's `/usr/bin/git` install shim to find out. Without it every
forum route answers `503` and items go on working.

### Routes

| Route | |
|---|---|
| `POST /forums` | a new, empty forum owned by the caller: `{id, git_url, item_id, created_at}` (`201`) |
| `PUT /forums/{id}/item` | `{item_id}`: link the caller's forum to a `project` item they created. `403` for a member who is not the forum's owner or did not create the item, `409` when the forum is linked already, the item already has a forum, or the item is not a project |
| `GET /items/{id}/forum` | the forum linked to an item the caller can see; `404` when it has none |
| `DELETE /forums/{id}` | delete an unlinked forum and its repository (`204`). `403` for a non-owner who can see it, `409` once it is linked |
| `GET /forums/{id}/pushes?after=&limit=` | the push records with a `seq` above `after` (default 0), at most `limit` (default 1000, up to 5000), oldest first |
| `/git/{id}.git/info/refs`, `git-upload-pack`, `git-receive-pack` | git's smart HTTP |

Each of these answers `503` when the server has no usable git. `git_url` is
`<base URL>/git/<id>.git`, built from the request's own base URL, so behind a gateway the
server must see the public scheme and path prefix (`VISTA_COLLAB_ROOT_PATH` and the gateway's
forwarded headers) or it hands out URLs clients cannot reach.

### Git over HTTP

The three git routes run `git http-backend` as a CGI, one process per request, after the
identity dependency and the forum's access check; the request body streams to its stdin and its
output streams back, never held whole. The forum id must be a UUID in its canonical spelling
before any path is built from it. `REMOTE_USER` is the caller's `sub` and the process gets a
clean environment (not the server's, and no user's git configuration). A gateway in front must
pass these requests through with their bodies streamed and not buffer, cap or time them out
early.

Repositories live under `data/forums/<id>.git`, created bare with
`receive.denyNonFastForwards`, `receive.denyDeletes` and `receive.advertisePushOptions` on, and
the two hooks below.

### Hooks and rules

Each repository has a `pre-receive` and a `post-receive` hook, both the server package's
`vista-collab-forum-hook` entry point
([`services/forum_hook.py`](../collaboration_server/src/vista_collaboration_server/services/forum_hook.py)),
run by the interpreter that runs the server. `pre-receive` refuses a push that would:

- update any ref other than `refs/heads/vista-forum/threads/<thread-id>`;
- delete a thread, or be anything but a fast-forward;
- add a `CLOSED` post to a thread whose root was pushed by another account;
- add a `RUN` post while the thread's latest run is live.

A run's liveness is the forum format's rule ([Runs](forum-git-format.md#runs)), timed by when
the server received the posts rather than their `ts`, so a pusher's clock cannot shift it. A
run expires after `VISTA_COLLAB_FORUM_RUN_EXPIRY_SECONDS` (default 2700, the backend's default
too). A refusal prints a sentence naming the rule, which git relays to the client as
`remote: Refused …`, and changes nothing.

A push with the `vista-import` push option may create threads the forum does not have yet, for
a project moved onto the forum: their posts are recorded as imported and the `CLOSED` and `RUN`
rules are not applied to them. It changes nothing for a thread that exists.

git tells a client its push was accepted before `post-receive` has run, so a push that passes
`pre-receive` leaves a marker until its `post-receive` is done, and the next `pre-receive`
waits (up to 10 s) for markers to clear. Otherwise an opener closing a thread right after
opening it could be judged before the server had recorded who opened it.

### Push records

`post-receive` writes one `forum_push` row for each post a push added, and one of kind
`THREAD` for each thread root (the only record of who opened a thread): `forum_id`,
`thread_id`, `post_id`, `kind`, `commit`, `pusher_sub`, `pusher_name`, `imported`,
`received_at`, and `seq`, the order the server took them. `GET /forums/{id}/pushes?after=<seq>`
serves them to anyone who may use the forum, so a VISTA backend can show who wrote each post
and ask only for what is new. A post copied in by an import is recorded `imported`, as pushed by
the account that moved it, and the backend shows no name for it.

### Copying and deleting

`POST /items/{id}/copy` of an item with a forum also makes a new, empty forum, owned by the
copier and linked to the copy, so the copy's team cannot reach the original's. The original's
forum is not touched. A linked forum cannot be deleted, so it stays as long as its item.

### Failures and a current limitation

A hook's stderr goes to the pushing client, so a hook that fails for any reason other than a
rule is logged, with the forum id and the traceback, to `data/forums/forum-hook-errors.log`.
`pre-receive` then refuses with a generic message ("The server could not check this push;
nothing was changed."); `post-receive` lets the already-accepted push stand. Look there first
when pushes fail without a rule's sentence.

The hooks read and write the server's database through SQLite directly, because they run as
git's synchronous child processes; the server passes them its database settings, and a hook
refuses to run against any other database, so every forum push would fail with the generic
message. This is how it works today, not a commitment to SQLite. Running the server on another
database engine will mean giving the hooks a synchronous connection to it, or having them ask
the server, and the hook tests in `tests/test_forums.py` are what will catch it.

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
| `/forums`, `/items/{id}/forum`, `/git/{id}.git/…` | hosted forums and git's protocol; see [Forums](#forums) |

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
links. On NFS, where SQLite's WAL does not work, set `VISTA_COLLAB_SQLITE_JOURNAL_MODE=DELETE`
(the forum hooks use it too). To host [forums](#forums) the host needs git 2.34 or later, and
the gateway must stream git's requests and pass on the public scheme and prefix.

[`collaboration_server/deploy/`](../collaboration_server/deploy/) holds VISTA's own deployment,
a systemd unit and its environment, as an example.
