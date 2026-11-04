## 1. Forum format and client

- [x] 1.1 `RUN` kind in `services/forum_git.py`: `start_run(thread, rounds, roster)` writes it with its `run` object; readers parse `run`; unknown or malformed `run` objects are skipped and logged. Verify with new cases in `backend/tests/test_forum_git.py`
- [x] 1.2 `verdict` field on `DONE`: written by the Referee's post, read and validated, falling back to the body. Verify in `test_forum_git.py`, including a `DONE` without the field
- [x] 1.3 `live_run(thread, now, expiry)`: the latest unended `RUN`, ended by its Referee's `DONE` or `BLOCKED` or by `CLOSED`, expired past `run_expiry_seconds`. Verify with table-driven unit tests (waiting run kept, abandoned run expired, failed run ended)
- [x] 1.4 `sync_forum`: one fetch of every thread ref, returning the threads whose tip moved. Verify with two working repositories against one bare remote in `test_forum_git.py` (Bob's install lists Alice's new thread)
- [x] 1.5 `forum.run_expiry_seconds` derived from `max_job_wait_seconds + 900` in `config.py`. Verify with a unit test

## 2. Database and projection

- [x] 2.1 `ForumThreadTable` and `ForumPostTable`, and nullable `DebateRunTable.forum_thread_id`, in `db/schemas.py`. Verify that `db/schema_check.py` passes against a database created by the previous release (`backend/tests/test_forum_db_columns.py`)
- [x] 2.2 `services/threads.py`: `project_forum_thread` fills `forum_thread`/`forum_post` idempotently, as `project_thread` does (concurrent writers, vanished posts kept as `on_remote = False`). Verify in `backend/tests/test_threads.py`
- [x] 2.3 Carry-over (design D10): link and fill existing runs on a project's first sync, missing threads from `debate_post`. Verify in `test_threads.py` with an old concluded debate and a missing one

## 3. Runs on any thread

- [x] 3.1 Orchestrator (`agents/forum/debate.py`): start writes the root, `forum_thread` and the first run; every `run()` posts its `RUN` (the rounds still to argue); Continue reuses this install's run in the thread (design D6); a crash posts the Referee's `BLOCKED`; DONE carries the verdict; every sync also projects the shared thread. Verified in `test_debate_driver.py` with `fake_forum`
- [x] 3.2 Continue on a thread this install did not open: `adopt` gives it a run and roster of this install's own (models from the request, so the defaults unless chosen), and `resume` argues it. The `409` while another run is live is the API's (group 4). Verified in `test_debate_driver.py` (adopted thread, distinct roster, newest verdict current)
- [x] 3.3 Simulations in a teammate's run use the runner's credentials and clusters: an adopted run goes through the same `continue_debate_task`, which builds its simulations from this install's settings; late results post as before. No new test: covered by the existing `test_debate_simulation.py` suite, which passes unchanged
- [x] 3.4 Referee prompt rules on the whole thread including earlier verdicts (`prompts/referee.md`); RUN markers are left out of the transcript and teammates' posts stay peers (`roles.render_transcript`). Verified in `test_debate_driver.py::test_agents_do_not_read_run_markers` and the unchanged `test_debate_roles.py`

## 4. API

- [x] 4.1 Thread-keyed routes in `api/debate.py` (design D8): list with throttled sync, view, post, runs, close (`403` unless opened here), events (stays open while the thread is viewed and not closed). Remove the run-keyed routes. Verified by porting `test_debate_api.py` to the new routes (57 pass), with new cases for a peer's thread: listed, posted to, adopted by Continue, Continue refused while a peer's RUN is live and accepted once it expires, Close refused
- [x] 4.2 Two-install end-to-end on real git: Alice opens and concludes, Bob lists it, posts, continues, concludes; both see Bob's verdict as current, and Bob's posts are peer-claimed on Alice's side. Verified in `backend/tests/test_threads.py::test_two_installs_share_a_thread_end_to_end`; Bob's refused close (403, no CLOSED post) in `test_debate_api.py`

## 5. UI

- [x] 5.1 `lib/debates.ts` and the `app/api/debates/*` proxies use thread ids and the new routes. Verified with `ui/tests/debates.test.ts` and the new `ui/tests/shared-threads.test.ts`
- [x] 5.2 `app/hypothesis-lab/page.tsx`: list of every thread with opener and status, polled every 30 s while mounted; view with the current verdict (marked when another install's Referee wrote it; earlier verdicts stay in the thread), the live run ("Another researcher is running 3 rounds · 2 posts so far · last 4 min ago"; no name on a forge), Continue offered only while no run is live, with a note saying why; Close only on threads opened here. The stream is open while someone argues; a quiet open thread is re-read every 15 s. Verified with `ui/tests/shared-threads.test.ts` and `ui/e2e-hermetic/hypothesis-lab.spec.ts` (hermetic Playwright, 5 pass)

## 6. Docs and checks

- [x] 6.1 `docs/forum-git-format.md` (`RUN`, the `verdict` field, liveness and expiry, Close by the opener) and `docs/hypothesis-forum-hosting.md` §5 (joining now works) and §6. Verify with `./scripts/ci-local.sh docs`
- [x] 6.2 `./scripts/ci-local.sh backend ui` and `openspec validate forum-shared-threads --strict` pass. Backend tests run as CI does, outside the checkout with no `.env`: 1697 passed; the 3 failures were `test_default_project_constants.py` reading `ui/`, which that copy left out, and pass in the worktree
