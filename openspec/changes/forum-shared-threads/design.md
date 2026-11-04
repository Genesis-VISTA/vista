## Context

See proposal.md for the why. As things stand on `main`:

- `debate_run` is both the thread and the agents arguing in it. The only code that creates one
  is `DebateOrchestrator.start` (`agents/forum/debate.py:164`), so a run row exists only on
  the install that opened the thread. `GET /{project}/debates` lists those rows
  (`services/debate.py:123`), and every other route looks one up by `run_id`.
- `debate_post` is a copy of the thread's posts plus local-only notes (`round_index`,
  `tools_used`, `model`, `authored_by`), keyed by `run_id`, which is `NOT NULL`.
- The forum is read only per thread: `_maybe_refresh` re-reads one debate at most every 10 s
  while it is viewed (`api/debate.py:180`), and the orchestrator reads its thread before each
  turn. `ForumClient.list_threads` is called only by the `prior_debates` tool.
- Continue extends the same run: same row, same roster, `rounds += extra`
  (`api/debate.py:428`). Close writes `CLOSED` from any install.
- The structured verdict is stored only in `debate_run.verdict`. The `DONE` post's body is
  its Markdown rendering, and the post's one attachment holds the Referee's tool receipts.
- `db/schema_check.py` accepts additive changes only. SQLite cannot relax `NOT NULL` on an
  existing column, and the check does not compare nullability, so relaxing it in the model
  would pass the check and then fail every insert of a `NULL` on existing installs.

## Goals / Non-Goals

**Goals:**
- A lab whose list, view, post, Continue and Close work for every thread on the forum, keyed
  by `thread_id`.
- Rules (one live run, Close only by the opener, current verdict) that every install derives
  the same way from git alone, so that `forum-collab-hosting` can enforce the same rules on the
  server.
- No migration: existing debates and older installs keep working.

**Non-Goals:**
- Making the rules hold on a forge against someone pushing by hand. VISTA follows them, and git
  cannot make others do so.
- Real-time updates. Polling stays (30 s for the list, 10 s for a viewed thread).

## Decisions

### D1: Thread and run are separate rows

- `forum_thread` (new): one row per thread on the project's forum, filled from git. It holds
  `project_id`, `thread_id` (unique together), `title`, `framing`, `created_by` (the opener's
  host id), `opened_here`, `status` (open, done, blocked, closed), `verdict`, `live_run` (JSON
  worked out from the thread: origin, roster, rounds, posts so far, last post `ts`), `tip` (the
  last ref sha read), `thread_missing` and `synced_at`.
- `debate_run` keeps its meaning as "a run this install executed" and gains a nullable
  `forum_thread_id`. Its `topic`, `framing` and `thread_id` stay for old rows and are written for
  new ones too, so an older version still reads a newer database.

Alternative: a placeholder `debate_run` for every discovered thread. Rejected because every
query would then have to tell real runs from placeholders, which is the mixing this change
removes.

### D2: `forum_post` is the post copy; `debate_post` keeps the local notes

`forum_post` (new) is keyed by `(forum_thread_id, post_id)` and holds what git holds, plus each
reader's computed fields (`vouch_lane`, `votes`, `peer_votes`, `on_remote`, and `verdict` for
`DONE`). It is filled by projecting the thread, as `project_thread` does today, and can be
dropped and rebuilt at any time. `debate_post` is unchanged and still written for this
install's own runs. The thread view reads `forum_post` and left-joins `debate_post` on
`post_id` for the notes.

Alternatives: relaxing `debate_post.run_id`, which fails on existing SQLite databases (see
Context); reading git on every view, which is too slow for a stream that polls every second.

### D3: The lab's list syncs the whole forum, throttled per project

`GET /{project}/threads` first runs `sync_forum(project)`, at most once every 30 s per project,
behind a per-project lock (the same pattern as `_maybe_refresh`). It does one `git fetch` of
`+refs/heads/vista-forum/*`, then projects only threads whose tip changed since `tip`. It does
not publish unpublished posts. The UI polls the list every 30 s while the lab page is mounted
and stops when it unmounts, so nothing fetches while no lab is open. A failed sync is logged,
leaves the rows as they are, and records the reason where the forum status already reports a
failed publish.

### D4: A run starts with a `RUN` post and ends on its Referee's `DONE` or `BLOCKED`

`RUN` is a new kind written only by its own verb, like `TASK` and `CLOSED`. It is posted as
`human` from the install starting the run, with a `run` object in the post file:
`{"rounds": n, "roster": {"proposer": id, "reviewer": id, "referee": id}}`. The run ends at the
first later `DONE` or `BLOCKED` posted by its `roster.referee`, or at `CLOSED`. So that a
failure always ends the run visibly, the orchestrator's run task posts `BLOCKED` as the Referee
from a `finally` whenever it ends without a verdict, a crash included. Older installs skip
`RUN` as an unknown kind, which the format already allows.

### D5: Liveness is worked out by each reader, with an expiry from settings

A thread's live run is its latest `RUN` that has not ended (D4), and whose newest post, the
`RUN` itself or any post by its roster, has a `ts` within `run_expiry_seconds`. That value is
`forum.max_job_wait_seconds + 900`: 45 minutes by default. This is the first use of `ts`
beyond display, and only as a bound. Clocks between installs differ by seconds or minutes,
against a 45-minute window. Continue syncs the thread, refuses with `409` naming the live run's
origin, and otherwise commits its `RUN` and publishes it before the first turn.

Two installs that both pass the check before seeing each other's `RUN` both run. The spec
allows it, the view shows both, and the later `DONE` wins. The hosted forum refuses the second
`RUN` at push time (`forum-collab-hosting`).

Alternative: heartbeat posts, which free a dead run sooner but add commits every reader has to
skip. Rejected in the grilling.

### D6: Every Continue is a new run; the roster is kept per install and thread

Continue creates a new `debate_run` row for the new `RUN`, with participants copied from this
install's latest run in the thread, so identities are reused (spec: per-role attribution).
When this install has no run in the thread, it mints a roster, with every role on the default
model. `rounds` and `rounds_done` are per run, and the view shows the live run's progress.

### D7: The structured verdict is a field of the `DONE` post

`posts/<id>.json` for a `DONE` gains `"verdict": {...}` (the Referee's verdict model dump).
Readers ignore unknown keys, so this needs no version bump. A reader validates the field. If
it is absent or invalid, it uses the body. `forum_thread.verdict` is the latest `DONE`'s.
`debate_run.verdict` is still written for this install's runs.

### D8: Routes are keyed by thread

| Before | After |
|---|---|
| `GET /debates` | `GET /threads`: sync (D3), then every `forum_thread` |
| `POST /debates` | `POST /threads`: open a thread and start its first run |
| `GET /debates/{run_id}` | `GET /threads/{thread_id}`: thread, posts, this install's runs, the live run |
| `POST /debates/{run_id}/posts` | `POST /threads/{thread_id}/posts` |
| `POST /debates/{run_id}/continue` | `POST /threads/{thread_id}/runs` |
| `POST /debates/{run_id}/close` | `POST /threads/{thread_id}/close`: `403` unless `created_by` is this install's host id |
| `GET /debates/{run_id}/events` | `GET /threads/{thread_id}/events` |

The event stream keeps the 1 s database poll and the 10 s forum refresh. It no longer ends when
this install's run ends, but stays open while the thread is viewed and not closed, so a
teammate's run streams in too. The Next.js proxies under `app/api/debates/*` follow.

### D9: Agents see teammates as peers

Nothing changes in the lanes. Bob's agents read Alice's roster's and Alice's own posts as
`peer-claimed`, presented as outside agents or people, not as their operator
(`agents/forum/roles.py`). Bob's operator posts are `host-observed` on his install. The
Referee's prompt says it rules on the whole thread, including earlier verdicts.

### D10: Carrying existing debates over

On a project's first sync after the upgrade, each `debate_run` without `forum_thread_id` gets
its `forum_thread` (by `thread_id`) and is linked to it. Its posts go into `forum_post` from git
or, for a missing thread, from its `debate_post` rows. A `RUN` is not written for old runs; a
thread with no `RUN` has no live run unless one of this install's runs is active locally. The
step is idempotent and runs inside the sync's lock.

## Risks / Trade-offs

- [Someone with push access writes a `RUN` that never ends, to block Continue] → It expires
  like any run. Push access already allows worse; the hosted forum refuses it.
- [Clock skew between installs shifts expiry] → A bound of minutes against a 45-minute window.
  The hosted forum uses its own receive time.
- [Two concurrent runs on a forge] → Allowed by spec, shown as two runs, newest verdict
  current.
- [A forum with many threads makes the list sync slow] → One fetch per sync; only threads whose
  tip moved are read.
- [Post text stored twice for own posts (`forum_post`, `debate_post`)] → Small, and it keeps
  both tables simple.
- [An older install on the same forum] → It skips `RUN` and ignores `verdict`. It still sees
  only its own threads and can still Close others' threads, as today.

## Migration Plan

Additive only: two new tables and one nullable column, which pass `db/schema_check.py`.
Existing debates are carried over lazily (D10). Rolling back to an older version leaves the new
tables unread. Debates started after the change stay readable there through `debate_run`, and
peers' threads disappear from the list again.
