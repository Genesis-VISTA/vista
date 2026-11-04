## Context

See proposal.md for the why. This change builds on:

- `forum-shared-threads`, which defines the `RUN` post, run liveness and expiry, Close only
  by the opener, and the thread-keyed lab. The server enforces those same rules.
- The collaboration server's sign-in and teams work (on `main` since `39d98a4f`). It adds an
  OIDC bearer token checked by `services/identity.py`, or the `dev-headers` mode on loopback
  for development and tests. It also adds teams that scope every item, `404` for anything the
  caller cannot see, `POST /items/{id}/copy`, and a backend that sends the token only over
  https or to loopback.
- `collaboration-projects`, whose project manifest carries `forum_repo_url`.
- The forum client already sends a credential as an `http.<url>.extraHeader` through
  `GIT_CONFIG_*` variables, scoped to the remote's URL (`forum_git._token_config`).

Today the server's design (D1 of `collaboration-server`) is that it reads neither manifests
nor blobs. A forum is different: it is a live repository the server must enforce rules on.

## Goals / Non-Goals

**Goals:**
- Forums on the server with no change to the forum format or the client's git usage beyond
  the credential.
- The rules of `forum-shared-threads` enforced where they can be: at push time on the server.
- Verified authorship from the push, not from anything a client writes.

**Non-Goals:**
- A web view of forums on the server. VISTA is the only reader.
- Server-side rules about post content beyond the kinds the rules need (`CLOSED`, `RUN`).

## Decisions

### D1: Forums are their own table and router, not items

Forums sit beside items, not among them: `forum` (`id`, `owner_sub`, `item_id` nullable and
unique, `created_at`) and `forum_push` (`forum_id`, `thread_id`, `post_id`, `kind`, `commit`,
`pusher_sub`, `pusher_name`, `imported`, `received_at`). A forum's team is its item's team,
looked up through the item. An unlinked forum is visible only to `owner_sub`. Routes:

| Route | |
|---|---|
| `POST /forums` | a new forum owned by the caller; returns `{id, git_url}` |
| `PUT /forums/{id}/item` | `{item_id}`: link once, owner only, the item must be a `project` the caller can see |
| `GET /items/{id}/forum` | the item's forum, if any |
| `DELETE /forums/{id}` | owner only, unlinked only (`409` otherwise) |
| `GET /forums/{id}/pushes?after=` | the push records, for verified names |
| `/git/{id}.git/*` | git's smart HTTP (D2) |

Alternative: a forum as an item of kind `forum`. Rejected because an item is immutable,
numbered versions the server never reads, and a forum is neither.

The manifest keeps `forum_repo_url` for forge forums. For a hosted forum, the backend writes
`{"forum": "hosted"}` instead of the URL, and import resolves it through
`GET /items/{id}/forum`. That way a copy, which carries the original's manifest, can't point
at the original's forum.

### D2: Git is served by `git http-backend`, behind the identity dependency

A FastAPI route for `GET /git/{id}.git/info/refs`, `POST /git/{id}.git/git-upload-pack` and
`POST /git/{id}.git/git-receive-pack`:

1. Resolves the caller with `require_identity` and the forum's access (D1). Anything else is
   `404`.
2. Runs `git http-backend` as a subprocess with the CGI variables (`GIT_PROJECT_ROOT`,
   `PATH_INFO`, `QUERY_STRING`, `REQUEST_METHOD`, `CONTENT_TYPE`, `GIT_HTTP_EXPORT_ALL=1`).
   `REMOTE_USER` is the caller's `sub`, and the caller's display name goes in
   `VISTA_PUSHER_NAME`.
3. Streams the request body to the subprocess's stdin and its CGI output back as a
   `StreamingResponse`, splitting the CGI headers from the body.

Repositories live under `data_dir/forums/<id>.git`, created bare with
`receive.denyNonFastForwards=true`, `receive.denyDeletes=true`,
`receive.advertisePushOptions=true` and the hooks of D3. The server requires git 2.34 or later
at startup, and only for forums: without git, the forum routes answer `503` and items keep
working.

Alternatives: dulwich (pure Python), which avoids the subprocess but reimplements git's
protocol; a separate git server with its own auth, which is a second service and identity
system.

### D3: Rules are a `pre-receive` hook; records are a `post-receive` hook

Both hooks are one entry point of the server package (`vista-collab-forum-hook pre|post`),
installed into each repository's `hooks/`. They read the server's settings and database.

- **`pre-receive`** checks every ref update against the rules in the `hosted-forum` spec. It
  reads the new commits' post files with `git` plumbing. A `CLOSED` post is refused unless
  `REMOTE_USER` is the `pusher_sub` recorded for the thread's root. A `RUN` post is refused
  if the thread's latest run is live, by the format's rule, with times taken from
  `forum_push.received_at`. Expiry is `VISTA_COLLAB_FORUM_RUN_EXPIRY_SECONDS`, default 2700
  (matching the client's default). A refusal prints the rule's message, which git relays to
  the client as `remote: …`.
- **`post-receive`** writes one `forum_push` row per post the push added.

A push with the `vista-import` push option (D5) may create threads that do not yet exist on the
forum. Their posts are recorded with `imported = true`, and `CLOSED`/`RUN` checks don't apply
to them. On SQLite over NFS the hooks use the configured journal mode, as the server does.

### D4: The backend sends the sign-in to the server's forum URLs only

`forum_config_for` recognises a forum URL under the configured collaboration server's address.
For such a URL, `_token_config` sends `Authorization: Bearer <sign-in token>`, or in
`dev-headers` mode the `X-Vista-Install-Id`/`X-Vista-Display-Name` headers, scoped to that
URL. It sends no forge token. It applies only over https or to loopback, matching the
collaboration client. A refused sign-in is a `401` from the server, reported as the collaboration
sign-in being refused. A rule refusal (`remote:` lines from D3) is reported by its message.

### D5: Life cycle in the backend

- **Create**: when the collaboration server is set and reachable with a sign-in, project
  creation calls `POST /forums` and stores its `git_url` as the project's forum URL. A failure
  creates the project with no forum and says why, so creating a project never fails on the
  server.
- **Publish**: the first publish of a project with a hosted forum calls `PUT /forums/{id}/item`
  after the item exists.
- **Import**: a manifest with `"forum": "hosted"` gets its URL from `GET /items/{id}/forum`.
- **Copy**: `POST /items/{id}/copy` on the server creates a new forum linked to the copy when
  the source item has one. This is the server's own record, so it reads no manifest.
- **Delete**: deleting a project calls `DELETE /forums/{id}` for an unlinked forum and
  ignores `409` for a linked one.
- **Move** (project settings): `POST /forums`, then push every local `vista-forum/threads/*`
  ref (after a sync of the old forum) with `-o vista-import`, then switch the project's URL.
  Any failure leaves the URL unchanged, and the empty forum is deleted.

### D6: Verified names come from push records, cached per post

On each thread sync, the backend asks `GET /forums/{id}/pushes?after=<last>` and stores
`pusher_name` on `forum_post` (a nullable column added by this change). The lab shows it with
a "verified" mark. Role prompts present such a post as "<name> (teammate, verified by the
server)" while keeping the "not your operator" framing of `agents/forum/roles.py`. Imported
posts get no name.

## Risks / Trade-offs

- [The gateway in front of the deployed server buffers or limits git's streamed bodies, or
  times them out] → Checked against the deployment (Holly behind Obsidian's APISIX) before
  release. It is a task outside PR CI.
- [Sign-in tokens expire and can't be renewed, so posts wait unpublished] → They publish at
  the next sync after a new token. The lab says which sign-in was refused and where to get a
  new one.
- [A hook that crashes refuses every push] → Hooks are tested against real git in CI. A hook
  error is logged with the forum id and refuses with a generic message, never a traceback.
- [Server disk grows with forums] → Forums are small text repositories. Deletion of unlinked
  forums is covered, and pruning is out of scope.
- [The server now runs subprocesses for requests] → One `git http-backend` per git request,
  with the forum id validated as a UUID before any path is built.

## Migration Plan

New server tables and a new nullable backend column. Both are additive. Existing projects
keep their forums until moved (D5). Rolling back the server makes hosted forums unreachable:
their posts stay on each install and publish again once the server returns. The backend can
roll back independently, since an older backend treats a hosted URL as an `https://` remote
with system git credentials, which the server refuses.
