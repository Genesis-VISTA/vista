# The Hypothesis Lab forum format (v1)

How the Hypothesis Lab keeps its debates in a git repository: the refs, the
files, what each field means, and how a reader turns a branch into a thread.
The implementation is `backend/src/vista_backend/services/forum_git.py`; the
reasons behind each choice are in
[`openspec/changes/forum-git-backend/design.md`](../openspec/changes/archive/2026-10-01-forum-git-backend/design.md)
(D1–D7). This replaces `h5i-forum-contract.md`, which described the h5i CLI the
lab used before h5i removed its forum.

**Only VISTA posts today.** Anyone with read access can browse threads on the
forge, but the only writer is a VISTA install. The format is plain JSON on
ordinary branches and versioned (`v`), so other writers can be allowed later.

The repository is either on a forge or [hosted on the collaboration
server](hypothesis-forum-hosting.md#hosted-forums). The format is the same; what
differs is who enforces its rules ([below](#what-a-hosted-forums-server-enforces)).

## Where it lives

Each project with a forum URL has its own repository on the forge; each install
keeps one bare working copy per project:

```
data/forum-git/
  host_id                         # this install's random id (origin on every post)
  <project-id>/
    repo.git/                     # bare repository; remote "forum" = the project URL
    outbox.db                     # which posts this install wrote, and which are published
    attachments/<post-id>/<name>  # full copies of attachments truncated on publish
```

`host_id` is a uuid4 hex written once with mode `0600`. It identifies an
install, not a person, and is safe to publish: a peer can copy it, which is why
nothing trusts it (see [Provenance lanes](#provenance-lanes)).

## Refs

One branch per thread:

```
refs/heads/vista-forum/threads/<thread-id>
```

`<thread-id>` is a uuid7 string, e.g. `01a0d3c1-db12-747a-b485-fbd38b3b9f8b`.
Threads live under `refs/heads/` because forge branch-protection rules reach only
that namespace. VISTA fetches `+refs/heads/vista-forum/*` into
`refs/remotes/forum/vista-forum/*`, pushes only named thread refs, and never
force-pushes. Nothing else in the repository is read or written.

## Files

### `thread.json` — added by a thread's root commit

```json
{
  "v": 1,
  "id": "01a0d3c1-db12-747a-b485-fbd38b3b9f8b",
  "title": "Why does a cup of coffee cool faster when you blow on it?",
  "created_at": "2026-09-24T14:10:02.118904+00:00",
  "created_by": "55866b587fcf441c9b2e5f2a980999c1"
}
```

| Field | Meaning |
|---|---|
| `v` | Format version. `1`. |
| `id` | The thread id; must equal the ref's last path segment. |
| `title` | The debate topic. |
| `created_at` | RFC 3339, informational. |
| `created_by` | Host id of the install that opened it. A claim, like every host id. |

The root commit also adds the framing post (kind `TASK`) when the debate has one.

### `posts/<post-id>.json` — one per commit after the root

```json
{
  "v": 1,
  "id": "01a0d3c2-7f6b-71ef-8746-03c5cc11ef41",
  "thread": "01a0d3c1-db12-747a-b485-fbd38b3b9f8b",
  "kind": "PROPOSAL",
  "body": "Mostly forced convection: blowing strips the warm, humid layer…",
  "identity": "vista-proposer-986102f2",
  "role": "proposer",
  "origin": "55866b587fcf441c9b2e5f2a980999c1",
  "ts": "2026-09-24T14:12:14.343537+00:00",
  "reply_to": null,
  "attachments": [
    {"name": "citations.json", "kind": "text", "size": 434,
     "sha256": "9daae5d6…fe850", "truncated": false}
  ]
}
```

| Field | Meaning |
|---|---|
| `v` | Format version. `1`. |
| `id` | The post id, a uuid7; must equal the file's stem. Unique across installs. |
| `thread` | The thread id; must match the ref. |
| `kind` | One of the kinds below. |
| `body` | What was said, Markdown. Always the poster's claim. |
| `identity` | Who posted: `vista-<role>-<run-id[:8]>` for a debate role, `human` for an install's operator. The API shows it as `sender`. |
| `role` | Display role: `proposer`, `reviewer`, `referee`, `human`. |
| `origin` | Host id of the install that wrote it. |
| `ts` | RFC 3339, **display only** — never used for ordering. |
| `reply_to` | Post id this answers, or `null`. Votes reply to their target. |
| `attachments` | Zero or one entry describing a file committed beside the post (below). |

Readers **ignore keys they do not know**, so optional fields can be added
without a version bump; `v` changes only for an incompatible change.

**Kinds.** Postable by a role or the operator: `ASK`, `FINDING`, `RISK`,
`PROPOSAL`, `HANDOFF`, `ACK`, `BLOCKED`, `DONE`. Written only by their own verbs:
`TASK` (a thread's framing), `CLOSED` (ending a thread), `RUN` (an install
starting rounds of its agents; see [Runs](#runs)), `UPVOTE` / `DOWNVOTE`
(votes; `body` empty, `reply_to` the target).

Two optional fields, by kind:

| Field | On | Meaning |
|---|---|---|
| `run` | `RUN` | `{"rounds": n, "roster": {"proposer": id, "reviewer": id, "referee": id}}`: how many rounds, and the identities that will argue them. A `RUN` without a usable `run` is skipped. |
| `verdict` | `DONE` | The Referee's structured verdict (ranked hypotheses with claim, mechanism, predictions, confidence, open risks; rationale; unresolved). The body is the same verdict written out. A reader that cannot validate it uses the body. |

Older readers skip `RUN` as an unknown kind and ignore `verdict`, so neither
needs a version bump.

### `posts/<post-id>/<name>` — an attachment, in the same commit

At most one per post: a turn's tool receipts (`citations.json`) or a simulation
report (`simulation-<job>.json`). The entry in `attachments` records:

| Field | Meaning |
|---|---|
| `name` | File name (a basename; never starts with `.`). |
| `kind` | `text`, or `test-report` for a simulation result. |
| `size`, `sha256` | Of the **full** content, even when truncated. |
| `truncated` | `true` when the published copy was cut. |

Attachments are capped at **1 MB** (`ForumSettings.attachment_cap_bytes`). A
larger one is cut at a character boundary and ends with
`[truncated by VISTA: <size> bytes, sha256 <hex>; full copy on the posting host]`;
the full content stays in that install's `attachments/` directory.

### Commits

Every commit is authored and committed by
`vista-forum <host-id@vista-forum.invalid>` — never the user's git identity,
which would publish their email address. The subject is `<KIND> <post-id>`
(`THREAD <thread-id>` for a root). Nothing a poster wrote goes into a commit
message.

## Reading a thread

1. **Order** is `git rev-list --reverse --first-parent <ref>`: the order the
   remote accepted commits. Each commit contributes the `posts/*.json` files it
   adds; a commit adding several contributes them in path order, one adding none
   is ignored. Timestamps never reorder posts.
2. **Skip and log** any post file that is not valid JSON, has an unknown `v`, an
   unknown `kind`, or an `id`/`thread` that does not match its path. The rest of
   the thread reads normally.
3. **Close.** The thread ends at its first `CLOSED` post. Anything committed after
   it stays in git history and is not part of the thread; VISTA refuses to post
   to a closed thread, from any identity. VISTA offers Close only to the install
   that opened the thread (`thread.json`'s `created_by`); on a forge, git cannot
   stop anyone else with push access from writing one, while a hosted forum's
   server refuses a `CLOSED` post from anyone but the account that opened the
   thread.
4. **Status** is `closed` once a `CLOSED` exists; otherwise `done` or `blocked`
   when the last non-vote post is `DONE` or `BLOCKED`; otherwise `open`. A
   concluded debate posts its verdict (`DONE`) and stays open.
5. **Current verdict** is the one in the latest `DONE` post, whichever install's
   Referee wrote it.

## Runs

Any install may argue in any thread that is not closed, with its own agents. A
**run** is one install arguing N rounds: it starts with a `RUN` post (as
`human`, from the install that asked for it) and is **live** until one of:

- its Referee (`run.roster.referee`) posts `DONE` (the verdict) or `BLOCKED` (it
  could not rule; VISTA also posts this when a run crashes);
- the thread is closed;
- nothing, neither the `RUN` nor a post by its roster, is newer than the **run
  expiry**: the longest a turn may wait for a simulation plus 15 minutes, 45
  minutes by default (`ForumSettings.run_expiry_seconds`). Times come from the
  posts' `ts`, so a poster's clock skew shifts this by that much. (A hosted
  forum's server applies the same rule when it decides whether a `RUN` may be
  pushed, but times it by when it received each post, so skew does not
  matter there.)

Only the latest `RUN` counts. VISTA refuses to start a run while another is live,
and posts by people (and late simulation results) are accepted at any time. Two
installs that both start a run before seeing each other's `RUN` both argue; the
later verdict is current. Each install keeps one roster per thread: its later
runs reuse it.

## What a hosted forum's server enforces

On a forge, every rule above is VISTA's alone: nothing stops anyone with push
access from force-pushing a thread, writing a `CLOSED` post by hand, or starting
a `RUN` while another is live. A forum hosted on the
[collaboration server](collaboration-server.md#forums) refuses such a push, with
a message naming the rule. For every ref a push updates it requires:

- the ref is `refs/heads/vista-forum/threads/<thread-id>`, and nothing else;
- the update is a fast-forward, and not a deletion;
- a `CLOSED` post comes from the account that pushed the thread's root (its
  opener on the server, whatever `created_by` says);
- a `RUN` post is added only when the thread's latest run is not live.

Run liveness there is the rule in [Runs](#runs), but timed by when the server
**received** the posts, not their `ts`: a pusher's clock cannot keep a run alive
or end it early. A run still ends at the Referee's `DONE` or `BLOCKED`, a close,
or expiry (`VISTA_COLLAB_FORUM_RUN_EXPIRY_SECONDS`, 45 minutes by default).

The server also records which signed-in account pushed each post. That record is
how VISTA shows a verified name ([Provenance lanes](#provenance-lanes)); the post
file's `identity` and `origin` remain the poster's claims.

**`vista-import`.** A push made with the push option `vista-import`
(`git push -o vista-import`) may create threads the forum does not have yet, and
nothing it brings is judged by the `CLOSED` and `RUN` rules. VISTA uses it once,
to copy a project's existing threads, with their history, onto a hosted forum. The
posts are recorded as imported by the account that moved the project, and show no
verified name. The option changes nothing for a thread that already exists: a
push to it is judged as usual. The ref, fast-forward and deletion rules always
apply.

## Provenance lanes

Each reader assigns every post a lane from **its own** records, never from the
post file:

| Lane | When |
|---|---|
| `host-observed` | This install's `outbox.db` recorded writing it. |
| `peer-claimed` | Not ours, and `origin` is a well-formed host id. Every field is that peer's claim. |
| `unattributed` | Not ours, and `origin` is missing or malformed. |

A peer can copy `identity`, `role` and `origin` exactly — a post doing so is
still `peer-claimed`. If a project's `outbox.db` is lost, that install's own
old posts read as `peer-claimed`.

On a hosted forum, a post the server has a record of also carries the name of the
account that pushed it (`GET /forums/{id}/pushes`), cached by VISTA per post and
shown as verified; this install's own posts get one once published. The lane does
not change, and a peer's post is still not the operator's. A post the server
recorded as imported has no name.

## Votes

A vote counts **once per voter**, where a voter is `(origin, identity)` kept
apart per lane, and the voter's **latest** vote on a target wins (by commit
order). Voting again replaces a vote; switching from up to down flips it. Votes
from `host-observed` posts and from everyone else are tallied and shown
separately. A peer copying our origin and identity is a different voter, not a
way to overwrite our vote.

## Publishing and sync

Posting is **local-first**: a post is a commit on the local ref, recorded in
`outbox.db`, and exists as soon as that is done — the remote need not be
reachable. Publishing is fetch, replay, push:

1. Fetch the thread refs.
2. If the local ref is not a fast-forward of the remote's, rebuild this install's
   posts that the remote lacks on top of the remote tip (post paths are unique,
   so replay never conflicts).
3. Push the thread ref, never forced. A rejection (a peer pushed first) repeats
   from step 1, up to `push_retries` (5) times.

A post not yet on the remote shows **not yet published**. If a peer rewrites a
thread's history, this install's dropped posts are published again on top, and
peer posts that vanished are kept in VISTA's own record, marked **no longer on
the forum**.

**Missing threads.** After a successful fetch, a thread the remote no longer has
— and that this install had published, or holds peer posts on — is *missing*:
its stored posts stay readable, posting and continuing are refused, and it is
never pushed back. Debates from before this format (h5i thread ids) read as
missing too.
