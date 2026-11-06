## Why

A project's Hypothesis Lab forum is a shared git repository, but only the install that opened
a thread can see it in the lab, post to it, or continue it. Another install on the same forum
sees nothing in its lab, and its agents read the thread only as precedent once it is
finished. The forum format, sync and provenance lanes already handle several writers
(`docs/hypothesis-forum-hosting.md` §5). What is missing is a lab in which researchers on
different installs, and their agents, take part in the same thread.

## What Changes

- A **thread** (shared, owned by git) is separated from a **run** (one install's agents arguing
  N rounds in it, local to that install). A thread has zero or more runs, at most one live at
  a time.
- The lab lists **every thread on the project's forum**, marked by who opened it. It fetches the
  forum when the lab opens and about every 30 s while it stays open. Nothing syncs in the
  background.
- **Anyone may post** to a thread that is not closed, at any time, including while someone
  else's run is live. A late simulation result posts the same way.
- **Anyone may Continue** a thread with their own proposer, reviewer and referee, their own
  models, skills, knowledge bases and HPC credentials. Continue is refused while another
  run is live. A run that posts nothing for the job-wait limit plus 15 minutes (45 minutes
  today) has expired.
- The newest verdict is the thread's current verdict, in every install's lab.
- **Only a thread's opener** is offered Close.
- A teammate's live run is shown from git: "Alice is running 3 rounds · 2 posts · last 4 min ago".
- Forum format v1 gains, additively, a `RUN` post that marks a run's start and a `verdict`
  field on `DONE` holding the structured verdict. Older installs skip the unknown kind and
  ignore the new field.
- **BREAKING** (backend API only; the UI is its only client): the debate routes are keyed by
  `thread_id` instead of the local `run_id`, under `/{project}/threads`.
- New local tables `forum_thread` and `forum_post` (copies of git, rebuildable from it) and a
  nullable `debate_run.forum_thread_id`. All additive under `db/schema_check.py`, and existing
  debates carry over.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `agent-forum`: threads are visible to and joinable by every install on the forum. Posting,
  Continue and Close rules change, a thread has many runs, a run's liveness and expiry are
  defined, and the verdict is carried in git.

## Impact

- Backend: `services/forum_git.py` (RUN kind, `verdict` field, listing and syncing all
  threads), `services/debate.py` (projection into the new tables), `agents/forum/debate.py`
  (runs on any thread, the RUN marker, the run ending), `agents/forum/roles.py` (a teammate's
  posts), `api/debate.py` (thread-keyed routes), `db/schemas.py`.
- UI: `app/hypothesis-lab/page.tsx`, `lib/debates.ts`, the `app/api/debates/*` proxies.
- Docs: `docs/forum-git-format.md`, `docs/hypothesis-forum-hosting.md`.
- Related: `forum-collab-hosting` (written alongside, applied after this change) hosts the
  forum on the collaboration server and enforces these rules there. Earlier design:
  `openspec/changes/archive/2026-10-01-forum-git-backend/design.md`.

## Non-goals

- Hosting the forum on the collaboration server, verified names, or server-enforced rules
  (`forum-collab-hosting`).
- A lighter "contributor" agent that joins someone else's run.
- Syncing or notifying while the lab is closed.
- Enforcing Close or one-run-at-a-time on a GitHub/GitLab forum beyond what VISTA itself
  does. Anyone with push access can still write any post.
- Letting a teammate's post instruct this install's agents.
- VISTAGuard.
