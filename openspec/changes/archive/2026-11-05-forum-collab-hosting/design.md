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
`pusher_sub`, `pusher_name`, `imported`, `received_at`, and `seq`, the order the server took
them, which is the `after=` cursor). A thread's root is recorded as a row of kind `THREAD`
whose `post_id` is the thread id: it is the only record of who opened the thread, since a
thread need not have a `TASK` post. Readers of the push records skip it. A forum's team is its
item's team, looked up through the item. An unlinked forum is visible only to `owner_sub`. A
non-owner who can see a linked forum gets `403` from link and delete. Routes:

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
at startup, and only for forums: without git, every forum route answers `503` and items keep
working. On macOS it never runs the `/usr/bin/git` install shim to find out (as the backend's
`git_check.py`). `git_url` is built from the request's base URL, so behind a gateway the
server must see the public scheme and prefix (`root_path` and forwarded headers).

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
to them. The option only applies to threads the push creates; an existing thread is judged as
usual. An imported thread's root is recorded as pushed by the account that moved the project,
so that account is its opener on the server. On SQLite over NFS the hooks use the configured
journal mode, as the server does.

git tells the client a push was accepted before it runs `post-receive`, so a quick second push
could be judged on records the first has not written yet (an opener closing a thread they just
opened would be refused). A push that passes `pre-receive` leaves a marker named by its
`receive-pack` process, `post-receive` removes it, and the next `pre-receive` waits for
markers to clear, up to 10 s. A push that loses the ref update after `pre-receive` (two pushes
from one tip) gets no `post-receive`, so a marker whose process has exited is dropped at once.

A hook error is logged with the forum id to `forums/forum-hook-errors.log`, since a hook's
stderr goes to the client. `pre-receive` then refuses with a generic message, and
`post-receive` lets the already-accepted push stand.

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
  server. On `POST /projects`, a blank `forum_repo_url` means a hosted forum, a URL means that
  repository, and `?hosted_forum_wanted=false` means none; the response's `forum_notice` says
  why no hosted forum was made.
- **Publish**: every publish of a project with a hosted forum calls `PUT /forums/{id}/item`
  after the item exists, ignoring `403`/`404`/`409`. The first links it; later ones are no-ops,
  and a project moved onto the server after its first publish is linked by its next. A project
  published as a new item gets no forum, since its forum is linked to the first item.
- **Import**: a manifest with `"forum": "hosted"` gets its URL from `GET /items/{id}/forum`.
- **Copy**: `POST /items/{id}/copy` on the server creates a new forum linked to the copy when
  the source item has one. This is the server's own record, so it reads no manifest.
- **Delete**: deleting a project calls `DELETE /forums/{id}` for an unlinked forum and
  ignores `409` for a linked one.
- **Move** (project settings): `POST /forums`, then push every local `vista-forum/threads/*`
  ref (after a sync of the old forum) with `-o vista-import`, then switch the project's URL.
  Any failure leaves the URL unchanged, and the empty forum is deleted. The route is
  `POST /projects/{name}/forum/host`.
- **Continue**: on a hosted forum, Continue posts its `RUN` to the server before it answers,
  so a run refused under the one-run rule is a `409` ("Another user is continuing this
  debate.") and nothing starts. A forge forum keeps posting the `RUN` from the run itself,
  local first.

### D6: Verified names come from push records, cached per post

On each thread sync, the backend asks `GET /forums/{id}/pushes?after=<last>` and stores
`pusher_name` on `forum_post` (a nullable column added by this change). The lab shows it with
a "verified" mark. Role prompts present such a post as "<name> (teammate, verified by the
server)" while keeping the "not your operator" framing of `agents/forum/roles.py`. Imported
posts get no name. The cursor into the push records lives in `pushers.json` beside the forum's
outbox, not in `vista.db`, so a database reset cannot leave it pointing past records the new
database never stored.

A post the server refuses under a rule, other than a `RUN`, stays unpublished and is retried
at each sync, as any refused post is. The lab's own rules (Close only for the opener) keep
the interface from writing one.

### D7: The UI reads hosting from the URL

The UI treats a forum URL `<configured server>/git/<uuid>.git` as hosted; the backend sends
no separate flag, and a forum on another server shows as a plain URL. A create whose response
carries a `forum_notice` stays on the projects page to show it instead of opening chat.
"Host on the collaboration server" acts on the saved forum, so it is disabled while the URL
field has unsaved edits, and choosing "A repository URL" requires one. A publish failure
names its cause by reason, never by message text: `refused` with the `collaboration`
credential is a refused sign-in (linked to the server's `/sign-in`), `no_access` is a forum
the account may not use, and `rule` is a refusal by the server's hooks.

## Risks / Trade-offs

- [The gateway in front of the deployed server buffers or limits git's streamed bodies, or
  times them out] → Checked against the deployment (Holly behind Obsidian's APISIX) before
  release, outside this change and outside PR CI.
- [Sign-in tokens expire and can't be renewed, so posts wait unpublished] → They publish at
  the next sync after a new token. The lab says which sign-in was refused and where to get a
  new one.
- [A hook that crashes refuses every push] → Hooks are tested against real git in CI. A hook
  error is logged with the forum id and refuses with a generic message, never a traceback.
- [The hooks read the server's database with `sqlite3` directly, because they run as git's
  synchronous child processes] → This is how it works today, not a choice of engine. The server
  passes the hooks its database settings, and a hook refuses a non-SQLite database
  (`forum_hook.Context.db`), so every forum push would fail with the generic message. Moving
  the server to another engine means giving the hooks a synchronous connection to it, or
  having them ask the server, and the hook tests in `test_forums.py` are what will catch it.
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
