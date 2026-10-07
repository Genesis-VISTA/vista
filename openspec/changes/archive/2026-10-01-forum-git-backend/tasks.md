Work on the `agent-forum` branch. Decisions are in design.md (D1–D11; D8 signing is out of scope) and the
requirements in specs/agent-forum/spec.md. Run backend checks from `backend/`
with `uv run --extra dev pytest …`; lint with `./scripts/ci-local.sh backend lint`.

## 1. Git prerequisite and scaffolding

- [x] 1.1 Add the git check (D9): resolve `git`, on macOS gate `/usr/bin/git` on `xcode-select -p`, require ≥ 2.34, cache the result; verify with `test_git_check.py` covering absent git, too-old git, and the macOS shim (stubbed `xcode-select` returning 1 and asserting `/usr/bin/git` is never executed)
- [x] 1.2 Update `ForumSettings` (D10): add `git_binary`, `push_retries=5`, `attachment_cap_bytes=1_048_576`; add `settings.forum_git_dir` (`data/forum-git/`). The h5i fields and `forums_dir` stay until 3.6 deletes h5i, so the suite stays green between groups; verify the settings tests and that `uv run vista-backend` gets through config and `init_db`
- [x] 1.3 Host id (D4): create `data/forum-git/host_id` once (uuid4 hex, mode 0600) and reuse it; verify with a test that two reads return the same id and a fresh data dir gets a new one
- [x] 1.4 Add the outbox (D4; now a per-project file, see 3.7) and the `published` / `on_remote` post columns (D5); verify `init_db` creates them on a fresh DB and `_add_missing_columns` adds the post columns to an existing one

## 2. Git forum client (D1–D3, D5, D7)

- [x] 2.1 Create the new client module (e.g. `services/forum_git.py`) with the kept models and errors from `h5i_forum.py` (`PostKind` minus `REVIEW_REQUEST`/`CLAIM`, `POSTABLE_KINDS`, `VouchLane`, `Post`, `Thread`, `ThreadSummary`, `SyncResult`, `ForumError`, `ForumDisabled`, `ForumCommandError`, `ThreadClosed`, `InvalidKind`) plus new `ThreadMissing`, and a slim `Participant(identity, role)`; verify the module imports cleanly and `ruff`/type checks pass
- [x] 2.2 Implement the git runner: argument-list subprocess, `cwd` = bare repo, `GIT_TERMINAL_PROMPT=0`, `LC_ALL=C`, fixed `vista-forum <host-id@vista-forum.invalid>` author, per-call timeout, `ForumCommandError` with `_collapse`; verify with a test that a failing command's error includes git's first stderr line
- [x] 2.3 Add the real-git test fixture: a temporary bare remote plus two clients (host A, host B) with separate data roots; verify an empty `list_threads` on both
- [x] 2.4 Implement `ensure_repo`/`set_remote`/`remote` (bare `repo.git`, remote named `forum`); verify set-then-read round-trips and an unreachable URL raises on the first fetch
- [x] 2.5 Implement `create_thread` (root commit with `thread.json` + `TASK` framing post) and `list_threads`; verify host B lists host A's thread after sync, newest first
- [x] 2.6 Implement `post_as`/`post_as_human` via plumbing with a per-thread lock and `update-ref` CAS, kind validation before any write, and an outbox row per post; verify `InvalidKind` creates no commit and a post is returned without a network call
- [x] 2.7 Implement `read_thread`: rev-list first-parent ordering, post parsing, skip-and-log for malformed, unknown-`v`, unknown-`kind` or mismatched files, `vouch` lanes from the outbox, truncation after the first `CLOSED`; verify tests for ordering, a malformed peer file, a peer copying host A's origin (stays `peer-claimed`), and a post after `CLOSED` being hidden
- [x] 2.8 Implement publish/`sync`: fetch into `refs/remotes/forum/…`, replay unpublished outbox posts onto the remote tip, push without force, retry up to `push_retries`, mark published; verify the two-host push race lands both posts, offline posting then flushing clears `published_at IS NULL`, and a force-pushed remote that dropped host A's post gets it re-published
- [x] 2.9 Implement `close_thread` and closed-thread refusal (after best-effort fetch, so a peer's close counts); verify posting after a local or peer `CLOSED` raises `ThreadClosed`
- [x] 2.10 Implement `vote` and `Thread.tally_split`/`tally` counting one vote per (origin, identity) with the latest winning; verify repeat and changed votes, and the observed/peer split
- [x] 2.11 Implement attachments: `stage_attachment` returns a handle, `post_as(attachment=…)` commits `posts/<id>/<name>` in the same commit, 1 MB cap with marker, size and SHA-256, full copy under `data/forum-git/<project-id>/attachments/`; verify a 2 MB receipt publishes ≤ 1 MB with the marker and the full copy matches the recorded SHA-256
- [x] 2.12 Implement `ThreadMissing` when neither local nor remote ref exists; verify reading and posting a deleted thread both raise it

## 3. Wire it in and remove h5i

Done together with section 4: deleting h5i (3.6) removes the box and
vote-policy calls the roster and forum status depend on, so neither
section is green without the other. `test_project_forum.py` runs real git
against a local bare remote; the orchestrator, API, simulation and
grounding tests use `tests/harness/fake_forum.py`.

- [x] 3.1 Point `agents/forum/project_forum.py` at the new client: `forum_config_for` also requires the git check, `ensure_forum` creates the bare repo, sets the remote and syncs; verify `test_project_forum.py` (moved to a fake or real-git client) passes, including a bad URL failing the save with git's error
- [x] 3.2 Replace imports of `services/h5i_forum` across `agents/forum/*`, `agents/campaign/wiring.py`, `services/debate.py`, `api/{debate,projects,api}.py`; verify `grep -rn h5i_forum backend/src` returns nothing
- [x] 3.3 Handle `ThreadMissing` in `services/debate.refresh_from_forum`, the detail endpoint and the event stream: stored posts, `thread_missing: true`, no retry loop, 409 on post/continue; verify with API tests for a deleted thread and for a run whose `thread_id` is an h5i-style id
- [x] 3.4 Project `published` / `on_remote` into `DebatePostTable` during refresh; verify an offline post projects as unpublished and flips after a sync
- [x] 3.5 Remove `WebReader`, `ENFORCING_TIERS`, `read_web_page` and `Grounding.browser` from `agents/forum/grounding.py` and `wiring.py`; verify `test_debate_grounding.py` passes with those cases deleted
- [x] 3.6 Delete `services/h5i_forum.py`, `tests/test_h5i_forum.py`, `tests/test_h5i_forum_live.py` and the fake h5i shim/fixtures; remove `ForumSettings.binary`, `box_profile`, `box_isolation`, `egress`, `vote_policy` and `settings.forums_dir` (deferred from 1.2); add `FakeForumClient` for orchestrator/API tests; verify `uv run --extra dev pytest -m "not live and not hpc and not sandbox"` is green and `grep -rni h5i backend/src` finds only intentional mentions (none expected)

- [x] 3.7 Found during 5.2: the outbox, as a `forum_outbox` table in `vista.db` written through its own connection, deadlocked with a caller's uncommitted write until SQLite's busy timeout (`simulation.post_result`: `update_step` flushes, then `post_as`; also a project save with posts waiting to publish). Moved to a per-project `outbox.db` beside `repo.git` (`FileOutbox`, D4); `migrate_columns.py` moves existing rows and drops the table; verified by a real-git test that posts while an app-DB write is held open

## 4. Roster without boxes or stints (D6)

- [x] 4.1 Remove `box_slug`, `box_id`, `policy_digest` from `DebateParticipantBase` and `box_id`, `policy_digest` from `DebatePostBase`; add a drop step for them to `scripts/migrate_columns.py` (idempotent, `--dry-run`); verify on a copy of a branch-era DB that inserts work after the script and a second run is a no-op
- [x] 4.2 Simplify `DebateOrchestrator.start`/`resume`/continue to one roster (`vista-<role>-<run-id[:8]>`), deleting stint suffixes, forum revoke in `_retire`, and multi-stint filtering; verify `test_debate_driver.py` shows a continued debate reusing its identities
- [x] 4.3 Drop `box_slug`/`box_id` from the campaign step spec and rebuild the late-result `Participant` from `commissioned_by`; verify `test_debate_simulation.py`'s post-back test posts under the commissioning identity
- [x] 4.4 Remove `vote_policy`/`set_vote_policy`/`enrollments` callers, `_apply_vote_policy`, `_enrolled_origins`, `DebateStatePublic.enrolled_origins`, and reshape `ForumStatus` to `{enabled, shared, remote, git_ok, git_reason, unpublished}`; verify the forum status API test covers git absent and unpublished counts

## 5. UI

- [x] 5.1 Read the relevant Next.js docs in `ui/node_modules/next/dist/docs/` before editing (AGENTS.md rule), then update `ui/lib/debates.ts` types: drop `box_id`, `policy_digest`, enrollment types and `votes_counting`; add `published`, `on_remote`, `thread_missing`, `git_ok`, `git_reason`; verify `npm run lint` and `npx tsc --noEmit`
- [x] 5.2 Update `ui/components/DebateThread.tsx` and `ui/app/hypothesis-lab/page.tsx`: remove box/policy chips and the "votes are being discarded" banner, show "not yet published" on unpublished posts, the "no longer on the forum" state with post/continue disabled, and the lab-off message with `git_reason` (e.g. "Git is not installed.") when `git_ok` is false; verify in the browser against a running backend (Playwright screenshot of each state)
- [x] 5.3 Update `ui/app/api/forum/status/route.ts` for the new `ForumStatus` and remove now-unused styles from `ui/app/globals.css`; verify `npm run lint` and the page still renders with a project that has no forum URL

## 6. Docs

- [x] 6.1 Replace `docs/h5i-forum-contract.md` with `docs/forum-git-format.md` (refs, `thread.json`, post fields, ordering, lanes, close, votes, attachment cap, "only VISTA posts today"); verify every field in design.md D2 is documented
- [x] 6.2 Rewrite `docs/hypothesis-forum-hosting.md` for plain git: create the repo, point a project at it, protect `refs/heads/vista-forum/**`, landing README, no h5i onboarding, git ≥ 2.34 required, old `h5i-forum/**` refs and `data/forums/` can be deleted; verify no remaining h5i instructions
- [x] 6.3 Update `docs/figures/hypothesis-lab.*` so a partner site takes part by running VISTA rather than "their own h5i box", and add a short forum section to `AGENTS.md` noting the git requirement; verify the figure text no longer mentions h5i

## 7. Integration check

- [x] 7.1 Run `./scripts/ci-local.sh` (backend, ui, mcp; lint + test) and confirm green
- [x] 7.2 Manual end-to-end on this Mac with two installs' data dirs (or two backend instances with different `data/` roots) against one local bare repo: start a debate on A, see it and post as a peer from B, close from B, confirm A shows "Ended by a peer"; record the result in this task. **Result (2026-09-24):** install A was a real stack (alternate ports, copy of the DB) on a scratch bare repo; A ran a real 1-round debate (converged, all posts published). Install B, a second data root driven through `forum_git.ForumClient` (the UI has no way to join another install's thread; see 7.4), listed and read A's thread (all `peer-claimed` from B), posted a FINDING and closed it. A's page showed both as `peer-claimed` from B's host id. Found: a *converged* run closed afterwards by a peer kept offering Continue and the posting box (both 409) — fixed; it now reads "Concluded · closed by a peer". "Ended by a peer" (close while arguing) is pinned by `test_a_peers_close_ends_the_debate`
- [x] 7.3 `openspec validate forum-git-backend --strict` passes
- [x] 7.4 Found in 7.2: a second install cannot open, post to or close another install's thread from the UI. Neither could the h5i lab (its endpoints were keyed by this install's runs too); a peer used the h5i CLI. **Decided (Sam, 2026-09-24): read-only peers for this change.** A peer install fetches every thread and its agents cite finished ones as precedent; people read threads on the forge. A "join a thread" view and a posting CLI are in design.md's Future work. The figure's partner panel is redrawn as "Another site reads the same forum", and its thread no longer shows a partner's post
