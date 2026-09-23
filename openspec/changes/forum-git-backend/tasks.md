Work on the `agent-forum` branch. Decisions are in design.md (D1–D11) and the
requirements in specs/agent-forum/spec.md. Run backend checks from `backend/`
with `uv run --extra dev pytest …`; lint with `./scripts/ci-local.sh backend lint`.

## 1. Git prerequisite and scaffolding

- [ ] 1.1 Add the git check (D9): resolve `git`, on macOS gate `/usr/bin/git` on `xcode-select -p`, require ≥ 2.34, cache the result; verify with `test_git_check.py` covering absent git, too-old git, and the macOS shim (stubbed `xcode-select` returning 1 and asserting `/usr/bin/git` is never executed)
- [ ] 1.2 Update `ForumSettings` (D10): remove `binary`, `box_profile`, `box_isolation`, `egress`, `vote_policy`; add `git_binary`, `push_retries=5`, `attachment_cap_bytes=1_048_576`; move `settings.forums_dir` to `data/forum-git/`; verify the settings tests and `uv run vista-backend` boot without h5i settings
- [ ] 1.3 Host id (D4): create `data/forum-git/host_id` once (uuid4 hex, mode 0600) and reuse it; verify with a test that two reads return the same id and a fresh data dir gets a new one
- [ ] 1.4 Add the `forum_outbox` table (D4) and the `published` / `on_remote` post columns (D5); verify `init_db` creates them on a fresh DB and `_add_missing_columns` adds the post columns to an existing one

## 2. Git forum client (D1–D3, D5, D7)

- [ ] 2.1 Create the new client module (e.g. `services/forum_git.py`) with the kept models and errors from `h5i_forum.py` (`PostKind` minus `REVIEW_REQUEST`/`CLAIM`, `POSTABLE_KINDS`, `VouchLane`, `Post`, `Thread`, `ThreadSummary`, `SyncResult`, `ForumError`, `ForumDisabled`, `ForumCommandError`, `ThreadClosed`, `InvalidKind`) plus new `ThreadMissing`, and a slim `Participant(identity, role)`; verify the module imports cleanly and `ruff`/type checks pass
- [ ] 2.2 Implement the git runner: argument-list subprocess, `cwd` = bare repo, `GIT_TERMINAL_PROMPT=0`, `LC_ALL=C`, fixed `vista-forum <host-id@vista-forum.invalid>` author, per-call timeout, `ForumCommandError` with `_collapse`; verify with a test that a failing command's error includes git's first stderr line
- [ ] 2.3 Add the real-git test fixture: a temporary bare remote plus two clients (host A, host B) with separate data roots; verify an empty `list_threads` on both
- [ ] 2.4 Implement `ensure_repo`/`set_remote`/`remote` (bare `repo.git`, remote named `forum`); verify set-then-read round-trips and an unreachable URL raises on the first fetch
- [ ] 2.5 Implement `create_thread` (root commit with `thread.json` + `TASK` framing post) and `list_threads`; verify host B lists host A's thread after sync, newest first
- [ ] 2.6 Implement `post_as`/`post_as_human` via plumbing with a per-thread lock and `update-ref` CAS, kind validation before any write, and an outbox row per post; verify `InvalidKind` creates no commit and a post is returned without a network call
- [ ] 2.7 Implement `read_thread`: rev-list first-parent ordering, post parsing, skip-and-log for malformed, unknown-`v`, unknown-`kind` or mismatched files, `vouch` lanes from the outbox, truncation after the first `CLOSED`; verify tests for ordering, a malformed peer file, a peer copying host A's origin (stays `peer-claimed`), and a post after `CLOSED` being hidden
- [ ] 2.8 Implement publish/`sync`: fetch into `refs/remotes/forum/…`, replay unpublished outbox posts onto the remote tip, push without force, retry up to `push_retries`, mark published; verify the two-host push race lands both posts, offline posting then flushing clears `published_at IS NULL`, and a force-pushed remote that dropped host A's post gets it re-published
- [ ] 2.9 Implement `close_thread` and closed-thread refusal (after best-effort fetch, so a peer's close counts); verify posting after a local or peer `CLOSED` raises `ThreadClosed`
- [ ] 2.10 Implement `vote` and `Thread.tally_split`/`tally` counting one vote per (origin, identity) with the latest winning; verify repeat and changed votes, and the observed/peer split
- [ ] 2.11 Implement attachments: `stage_attachment` returns a handle, `post_as(attachment=…)` commits `posts/<id>/<name>` in the same commit, 1 MB cap with marker, size and SHA-256, full copy under `data/forum-git/<project-id>/attachments/`; verify a 2 MB receipt publishes ≤ 1 MB with the marker and the full copy matches the recorded SHA-256
- [ ] 2.12 Implement `ThreadMissing` when neither local nor remote ref exists; verify reading and posting a deleted thread both raise it

## 3. Wire it in and remove h5i

- [ ] 3.1 Point `agents/forum/project_forum.py` at the new client: `forum_config_for` also requires the git check, `ensure_forum` creates the bare repo, sets the remote and syncs; verify `test_project_forum.py` (moved to a fake or real-git client) passes, including a bad URL failing the save with git's error
- [ ] 3.2 Replace imports of `services/h5i_forum` across `agents/forum/*`, `agents/campaign/wiring.py`, `services/debate.py`, `api/{debate,projects,api}.py`; verify `grep -rn h5i_forum backend/src` returns nothing
- [ ] 3.3 Handle `ThreadMissing` in `services/debate.refresh_from_forum`, the detail endpoint and the event stream: stored posts, `thread_missing: true`, no retry loop, 409 on post/continue; verify with API tests for a deleted thread and for a run whose `thread_id` is an h5i-style id
- [ ] 3.4 Project `published` / `on_remote` into `DebatePostTable` during refresh; verify an offline post projects as unpublished and flips after a sync
- [ ] 3.5 Remove `WebReader`, `ENFORCING_TIERS`, `read_web_page` and `Grounding.browser` from `agents/forum/grounding.py` and `wiring.py`; verify `test_debate_grounding.py` passes with those cases deleted
- [ ] 3.6 Delete `services/h5i_forum.py`, `tests/test_h5i_forum.py`, `tests/test_h5i_forum_live.py` and the fake h5i shim/fixtures; add `FakeForumClient` for orchestrator/API tests; verify `uv run --extra dev pytest -m "not live and not hpc and not sandbox"` is green and `grep -rni h5i backend/src` finds only intentional mentions (none expected)

## 4. Roster without boxes or stints (D6)

- [ ] 4.1 Remove `box_slug`, `box_id`, `policy_digest` from `DebateParticipantBase` and `box_id`, `policy_digest` from `DebatePostBase`; add a drop step for them to `scripts/migrate_columns.py` (idempotent, `--dry-run`); verify on a copy of a branch-era DB that inserts work after the script and a second run is a no-op
- [ ] 4.2 Simplify `DebateOrchestrator.start`/`resume`/continue to one roster (`vista-<role>-<run-id[:8]>`), deleting stint suffixes, forum revoke in `_retire`, and multi-stint filtering; verify `test_debate_driver.py` shows a continued debate reusing its identities
- [ ] 4.3 Drop `box_slug`/`box_id` from the campaign step spec and rebuild the late-result `Participant` from `commissioned_by`; verify `test_debate_simulation.py`'s post-back test posts under the commissioning identity
- [ ] 4.4 Remove `vote_policy`/`set_vote_policy`/`enrollments` callers, `_apply_vote_policy`, `_enrolled_origins`, `DebateStatePublic.enrolled_origins`, and reshape `ForumStatus` to `{enabled, shared, remote, git_ok, git_reason, unpublished}`; verify the forum status API test covers git absent and unpublished counts

## 5. UI

- [ ] 5.1 Read the relevant Next.js docs in `ui/node_modules/next/dist/docs/` before editing (AGENTS.md rule), then update `ui/lib/debates.ts` types: drop `box_id`, `policy_digest`, enrollment types and `votes_counting`; add `published`, `on_remote`, `thread_missing`, `git_ok`, `git_reason`; verify `npm run lint` and `npx tsc --noEmit`
- [ ] 5.2 Update `ui/components/DebateThread.tsx` and `ui/app/hypothesis-lab/page.tsx`: remove box/policy chips and the "votes are being discarded" banner, show "not yet published" on unpublished posts, the "no longer on the forum" state with post/continue disabled, and "needs git" when `git_ok` is false; verify in the browser against a running backend (Playwright screenshot of each state)
- [ ] 5.3 Update `ui/app/api/forum/status/route.ts` for the new `ForumStatus` and remove now-unused styles from `ui/app/globals.css`; verify `npm run lint` and the page still renders with a project that has no forum URL

## 6. Docs

- [ ] 6.1 Replace `docs/h5i-forum-contract.md` with `docs/forum-git-format.md` (refs, `thread.json`, post fields, ordering, lanes, close, votes, attachment cap, "only VISTA posts today"); verify every field in design.md D2 is documented
- [ ] 6.2 Rewrite `docs/hypothesis-forum-hosting.md` for plain git: create the repo, point a project at it, protect `refs/heads/vista-forum/**`, landing README, no h5i onboarding, git ≥ 2.34 required, old `h5i-forum/**` refs and `data/forums/` can be deleted; verify no remaining h5i instructions
- [ ] 6.3 Update `docs/figures/hypothesis-lab.*` so a partner site takes part by running VISTA rather than "their own h5i box", and add a short forum section to `AGENTS.md` noting the git requirement; verify the figure text no longer mentions h5i

## 7. Integration check

- [ ] 7.1 Run `./scripts/ci-local.sh` (backend, ui, mcp; lint + test) and confirm green
- [ ] 7.2 Manual end-to-end on this Mac with two installs' data dirs (or two backend instances with different `data/` roots) against one local bare repo: start a debate on A, see it and post as a peer from B, close from B, confirm A shows "Ended by a peer"; record the result in this task
- [ ] 7.3 `openspec validate forum-git-backend --strict` passes

## 8. SSH signing (final phase, D8)

- [ ] 8.1 Confirm which forge endpoints list SSH signing keys (GitHub `/<login>.keys` vs `GET /users/<login>/ssh_signing_keys`, GitLab, `code.ornl.gov`) and record the answer in design.md; verify by fetching keys for a known account on each
- [ ] 8.2 Sign post commits with `gpg.format=ssh` using the user's agent key selected in settings, or the VISTA-generated fallback key whose public half the UI shows for upload; fill `signer`; verify with a real-git test using a throwaway key loaded into a temporary `ssh-agent`
- [ ] 8.3 Verify peer signatures against cached forge keys via an allowed-signers file and `git verify-commit`; show "signed by <login>" only on success; verify tests for a valid signature, a forged signer claim, and an unsigned post
- [ ] 8.4 Add the optional per-signer vote count alongside the per-identity tally; verify a signed account voting through two identities counts once in the per-signer view
- [ ] 8.5 Document signing in `docs/forum-git-format.md` and the hosting doc; verify the setup steps by following them on a fresh data dir
