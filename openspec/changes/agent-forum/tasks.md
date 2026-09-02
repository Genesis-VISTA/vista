Each numbered section is one focused MR, reviewed and committed before the next
starts. Sections 1–2 are landed by the Step 0/1 commit.

## 1. Verified CLI contract

- [x] 1.1 Install h5i (v0.3.8, `~/.local/bin`) and record `h5i box probe` tiers
- [x] 1.2 Probe host vs box sides; establish that host-side `post` has no `--as`
- [x] 1.3 Verify box-relay posting yields distinct host-stamped identities
- [x] 1.4 Capture `forum read --json` / `list --json` shapes as the parser contract
- [x] 1.5 Establish that an unknown `--kind` exits 0 and is then silently dropped
- [x] 1.6 Establish that attachments must live in the box work dir; verify `fetch` round trip
- [x] 1.7 Establish `close` semantics (box-side exit 1) and vote statefulness
- [x] 1.8 Write `docs/h5i-forum-contract.md`

## 2. Proposal

- [x] 2.1 `proposal.md`, `design.md`, `specs/agent-forum/spec.md`, `tasks.md`
- [ ] 2.2 `openspec validate --all` green — **blocked: the `openspec` CLI is not installed on this host** (not on PATH, not resolvable via npx). Run once available.

## 3. Forum client (MR 1)

- [x] 3.1 `ForumSettings` in `config.py`: binary, forum root, profile, tier, egress allowlist, timeout, `enabled`
- [x] 3.2 `services/h5i_forum.py`: async `create_subprocess_exec` wrapper, argument lists only
- [x] 3.3 Pydantic models for thread / post / participant / vouch lane; kind enum
- [x] 3.4 Local kind validation before spawn (spec: unknown kind rejected)
- [x] 3.5 Confirm-by-reading after every post; surface a dropped post as failure
- [x] 3.6 Attachment staging into the box work dir + relative-name attach
- [x] 3.7 Per-role lock around read-then-vote; `post --reply-to` everywhere
- [x] 3.8 Participant setup/teardown: `box create` → `forum attach` → `revoke` + `box rm`
- [x] 3.9 Fake `h5i` shim + fixtures recorded from the contract doc
- [x] 3.10 `backend/tests/test_h5i_forum.py` — hermetic; `test_h5i_forum_live.py` marked `live` and verified against real h5i v0.3.8

Two contract details were found only by running MR 1's live suite, and are now
in `docs/h5i-forum-contract.md` §2.1–2.2: `box run … -- <cmd>` needs the h5i
binary named explicitly (else execvp, exit 71), and `box run` relays the inner
command's stderr onto the host's *stdout*.

## 4. Persistence (MR 2)

- [x] 4.1 `DebateRunTable` / `DebateParticipantTable` / `DebatePostTable` in `db/schemas.py`
- [x] 4.2 Keep `vouch` lane a distinct column — do not merge into the post row
- [x] 4.3 `services/debate.py` mirroring `services/campaign.py` (ValueError, not HTTP)
- [x] 4.4 Projection from `forum read --json`, idempotent on replay
- [x] 4.5 Tests on the in-memory DB fixture (`backend/tests/conftest.py`)

## 5. Role agents (MR 3)

- [x] 5.1 `agents/forum/roles.py` + prompt files for Proposer / Reviewer / Referee
- [x] 5.2 `Hypothesis` structured output: claim, mechanism, falsifiable predictions, confidence, open risks
- [x] 5.3 Reviewer prompted to falsify; upvote instead of posting bare agreement
- [x] 5.4 Peer-posts-are-data guard in every role prompt
- [x] 5.5 `FunctionModel` tests — no live LLM in PR CI

## 6. Orchestrator (MR 4)

- [x] 6.1 `agents/forum/debate.py` round loop, budget default 5, client + agents injected
- [x] 6.2 Referee `DONE` verdict at budget exhaustion
- [x] 6.3 Re-read between rounds; inject `human` posts into next round context
- [x] 6.4 Treat close (box-side exit 1) as expected termination, not an error
- [x] 6.5 Driver tests with fakes only, following `test_campaign_driver.py`

## 7. Grounding (MR 5)

- [x] 7.1 Per-role tool grants: RAG/KBs, salt-chemistry + neutronics skills.
      **Corrected in section 15** — the skills half shipped, the RAG half never did:
      `build_grounding` set no `rag`, so `search_literature` was never granted to any
      debate and the Reviewer ran with `prior_debates` alone.
- [x] 7.2 Prior closed threads on the same forum as readable context
- [x] 7.3 User-uploaded papers via the existing uploads/files service. **Wired in
      section 17** — it was unwired for two rounds after RAG for no technical reason,
      only because it sat outside what was asked each time.
- [x] 7.4 `h5i browser` reads under an egress allowlist; receipt attached to the citing post — **refuses to run below `container`/`microvm`**, because the allowlist does not bind at lower tiers (contract §5.1)
- [ ] 7.5 Assert a refused fetch stays visible in the record — **blocked on this host**: no `container`/`microvm` tier (no rootless Podman), and the h5i engine's HTTPS fails here, so a real allowlist refusal cannot be observed. Hermetic tests cover the refusal path; verify on a Linux host with Podman.

## 8. API (MR 6)

- [x] 8.1 `api/debate.py` at `/projects/{name}/debates` — create, list, get, close
- [x] 8.2 Human-post endpoint (host-side post, attributed `human`)
- [x] 8.3 SSE stream for live posts
- [x] 8.4 Register router in `api/api.py`; project membership as the access boundary
- [x] 8.5 Route tests

## 9. UI (MR 7)

- [x] 9.1 Read `ui/node_modules/next/dist/docs/` before writing any Next.js (AGENTS.md)
- [x] 9.2 Debate thread view rendering the host-stamped / agent-claimed boundary
- [x] 9.3 Role badges, kind chips, vote tallies, attachment links
- [x] 9.4 Live SSE + human post box + close control
- [x] 9.5 Verdict panel for the Referee's ranked hypothesis
- [x] 9.6 `npm run lint` clean + `tsc --noEmit` clean; page verified rendering in a browser against a stub backend
- [ ] 9.7 Vitest for `ui/lib/debates.ts` (`debateRoleOf`) — **deferred to `milestone-c-scientific-tools` §5**, which owns introducing Vitest and the `ui:test` CI job. Adding a second test-runner setup here would collide with that open change.

## 10. HPC in the loop (MR 8 — separable)

- [x] 10.1 Proposer/Reviewer may commission a job to test a prediction (the Referee may not — it rules, it does not gather evidence)
- [x] 10.2 Finished job posts back as `FINDING` with a `test-report` attachment, under the commissioning identity
- [x] 10.3 Hermetic tests (fake HPC + fake h5i) run in PR CI; nothing here needs a cluster
- [x] 10.4 Monitor orphan rule exempts debate-domain runs (they never have a chat session)
- [x] 10.5 Roster stays attached while jobs are in flight; collector reaps it afterwards
- [x] 10.7 HPC wired live: allowlist = project skills ∩ `hpc_jobs/`, clusters from the opener's credentials, cap of 2 per debate, tool ungranted when either is empty
- [ ] 10.6 End-to-end against a real cluster — **not run**: needs `hpc` credentials this host does not have

## 11. Close out

- [ ] 11.1 `README.md` + `AGENTS.md` sections for the forum feature
- [ ] 11.2 `./scripts/ci-local.sh` green across backend / ui / mcp
- [ ] 11.3 `openspec` sync + archive


## 12. Federation (upstreamed forum)

- [x] 12.1 Identity decided by vouch lane, not the sender field — an external human
      arrives as `sender="human"` by default, and a peer may wear a role identity
- [x] 12.2 Federation verbs in the client: `remote --branch-refs`, `sync`, `policy`,
      `enrollments`; contract measured with two hosts (`docs/h5i-forum-contract.md` §8)
- [x] 12.3 Peer posts surface between rounds — throttled forum refresh driven by the stream
- [x] 12.4 Closure attributed to operator or peer, derived from the CLOSED post's lane
- [x] 12.5 Outside person vs outside agent distinguished in the transcript; Referee told
      to rank reasoning and not claimed identity
- [x] 12.6 Peer votes counted and displayed apart from this forum's own
- [x] 12.7 Hosting runbook (`docs/hypothesis-forum-hosting.md`) + `ensure_federation`
      applying `remote_url` / `vote_policy` at startup, with the reachability check
      and the unenrolled-principal guard
- [x] 12.8a `ensure_federation` live-verified against real h5i + a real remote: the
      configured remote is applied, threads land under `h5i-forum/**`, and `principal`
      is refused while nobody is enrolled
- [x] 12.9 Forum status endpoint + UI banner when votes are being discarded
- [x] 12.10 Test DB moved off the shared-connection StaticPool, which had made two
      tests pass for the wrong reason
- [x] 12.8b Verified against a real forge (`jqyin/vista-hypothesis-forum`, private,
      SSH): auth and publish work; only forum refs are pushed (`main` and the three
      `h5i/env/human/*` box branches stayed local); a peer with no prior state reads
      both threads and sees every post labelled `peer-claimed`
- [ ] 12.8c Ref protection refusing a force-push — **blocked, not deferred**. GitHub
      returns 403 "Upgrade to GitHub Pro or make this repository public" for both the
      rulesets API and classic branch protection, so a free private repo cannot have
      ref protection at all. The unprotected case was measured instead: force-push
      backwards and delete are both accepted. Needs a repo on a plan offering rulesets.
      See `docs/hypothesis-forum-hosting.md` §3.
- [x] 12.11 Enrollment live: this host enrolled as `github.com/user/19734876` (jqyin),
      signature verified against the forge, published; vote policy set to `principal`
      and `/forum/status` now reports `votes_counting: true`
- [x] 12.12 `Enrollment` corrected against real h5i — the login is `display_name`,
      and `verified` dropped because `--verify` never reaches `--json`
      (`docs/h5i-forum-contract.md` §8.4)
- [x] 12.13 `/forum/status` decides `shared` from h5i's actual remote, not the
      setting, so an unreachable-at-boot remote is not advertised as shared
- [x] 12.14 `scripts/ci-local.sh` no longer reports success with lint errors:
      `run_section` called each section inside `if !`, which suppresses `set -e`
      through the whole call tree. Under `--fast` — the pre-commit hook's path —
      backend lint could not fail at all.
- [x] 12.15 A peer's post on a *finished* debate is visible. The event stream ends at
      a terminal status and the UI opens none for a finished run, so the forum refresh
      — which lived only inside that stream — never ran again; a peer commenting on a
      concluded hypothesis, the likeliest moment for one to, was invisible permanently.
      The detail endpoint now refreshes under the same throttle, and the page keeps
      polling a concluded-but-open debate (not a `closed` one — h5i's attic takes no
      posts). Reproduced with a failing test first.
- [x] 12.16 Measured that a host-side read fetches from the remote: ~1.5 s against
      GitHub over SSH vs 0.24 s local (`docs/h5i-forum-contract.md` §8.5)
- [x] 13.1 Author naming. Our own posts carry the VISTA account that wrote them,
      stamped on the authenticated path and preserved across every replay. Peer
      posts stay anonymous — h5i stamps `sender="human"` for every host's operator,
      so nothing in one is knowledge — and enrolled origins are returned separately
      as a machine→account map, worded as "from X's machine" rather than "X wrote
      this", since anyone with access to an enrolled machine posts from it.
- [x] 13.2 `scripts/migrate_debate_columns.py`: the app creates tables with
      `create_all`, which never adds a column to an existing table, so a live
      `debate_post` would fail at read time on `authored_by`. Additive, idempotent,
      `--dry-run`; applied to the local database.
- [x] 13.3 Continue an open debate for N more rounds (default 3, user-set).
      Attaches a fresh roster under stint-suffixed identities because the previous
      one was revoked at conclusion and a revoked identity cannot post; raises the
      budget rather than resetting `rounds_done`; refused while still arguing and on
      a closed thread. The simulation cap is per debate and is deliberately not
      refreshed, so continuing cannot buy more cluster time a round at a time.
- [x] 13.4 `_participants` filters to the active roster. Pinned with a test whose
      retired identity sorts *after* the live one — with real continuation names the
      unfiltered version passes by luck of string ordering, so the mutation survived
      until the test was written to defeat that.
- [x] 13.5 `resume` re-reads the run after its checkpoint. The commit that makes a
      continued debate visible to a viewer also expires the row, and handing that
      object to `run` made its first line async IO — the MissingGreenlet the user
      hit. Missed because the driver harness used a no-op checkpoint; it now commits
      by default, so every driver test runs the semantics production runs under.
- [x] 13.6 `_retire` deactivates the row even when h5i refuses the removal. They are
      an external side effect and a row write, not one operation: under a single
      `try`, an identity h5i had already revoked left our row claiming `active`
      forever. Observed in the live database — six participants marked attached, three
      of them revoked in the forum's own roster.
- [x] 13.7 `resume` clears *every* attached stint, keyed by identity rather than by
      role. Crashed resumes accumulate rosters, and a role-keyed retire clears one per
      role however many are attached.
- [x] 13.8 `run_job` in `scripts/ci-local.sh` reported real failures as
      `fail: … (exit 0)`. A compound `if` whose condition fails and which has no
      `else` returns 0, so `$?` afterwards was the if statement's status, not the
      command's. A failure line that names exit 0 reads as a harness bug and invites
      disbelieving the failure.

## 14. Register and evidence

- [x] 14.1 Proposer and Reviewer write as colleagues: brief, sharp, specific, with
      word budgets in both the prompt and the schema field descriptions (the model
      sees the latter). Referee keeps the formal register and is now told *why* the
      contrast exists — its verdict is the artefact someone cites later.
- [x] 14.2 `Hypothesis.to_post_body` drops the bold section headings for short inline
      labels. A form invites being filled in like one; two sentences under
      `**Mechanism.**` still read as a submission. `Verdict.to_post_body` composes its
      own formal shape from the same fields, so the ruling was unaffected.
- [x] 14.3 Register pinned by test — a proposal carries no `**`/`#`, a verdict keeps
      `## Verdict`. It is a product decision a later renderer edit would silently undo.
- [x] 14.4 Receipts persisted onto the post. `ToolCall.receipt` was written only to an
      h5i attachment that nothing could open, and `record_post_tools` dropped it — so
      the UI had "consulted the corpus" with no way to see what came back. Capped at
      8000 characters with the truncation marked, since it is read on every thread load.
- [x] 14.5 Every reading tool now records what it saw (query + passages, skill body,
      paper, prior threads), and `commission_simulation` records the job id and cluster
      — without those a reader has "a simulation was run" and no way to check that the
      run behind a FINDING is the run that was claimed.
- [x] 14.6 UI shows a receipt-backed call as a collapsed disclosure that opens to the
      full record. Backward compatible: rows written before this carry no `receipt` key
      and render as plain chips, so no migration is needed.
- [x] 14.7 A refused tool call is recorded too, marked `refused`. This reverses an
      earlier assertion that a refused fetch recorded nothing — right about citations,
      wrong about provenance, now that `tool_calls` feeds both. An agent blocked from
      checking a source must not look like one that never looked.

## 15. Corpus and simulation visibility

- [x] 15.1 `search_literature` is actually granted. `build_run_grounding` wires
      `rag_search` over the vista MCP server from the run's project and opener, the
      way `build_simulation` already did for HPC. A project with no knowledge bases
      gets no tool at all — a search over an empty corpus answers "nothing found" to
      every question and a role reads that as evidence of absence.
- [x] 15.2 A `kb_slug` the project does not list is refused before reaching MCP. The
      model picks that argument, so the project's KB list is the access boundary for
      a debate exactly as it is for a chat.
- [x] 15.3 `DebateDeps.knowledge_bases` is populated. It existed and the tool read
      it; nothing ever set it, so even a wired RAG would have had no default corpus.
- [x] 15.4 `commissioned_runs` + a UI panel: every job a debate commissioned, its
      scheduler state, and whether the result was posted back. A provenance chip
      proved only that a job was *submitted*; what became of it lived on the campaign
      side with no join between them.
- [x] 15.5 `last_polled_at` surfaced distinctly. A job nothing has looked at since
      submission is not a job running slowly, and the state alone cannot tell them
      apart — which is the case that prompted this.

## 16. Hypothesis Lab

- [x] 16.1 Renamed "Hypothesis Forum" → "Hypothesis Lab": nav label, page heading, and
      the route (`/hypothesis-lab`). The hosting doc's prose follows; its
      `jqyin/vista-hypothesis-forum` references are a real repository slug and stay.
- [x] 16.2 The Proposer writes its own post. `Hypothesis.note` is published verbatim
      and `to_post_body` adds nothing. Assembling the fields into claim → *Why:* →
      *Testable:* → *Confidence* gave every proposal in every round the same
      silhouette, so a fifth-round reply to one objection arrived shaped like a fresh
      submission — a form refilled, which is not how anyone brainstorms.
- [x] 16.3 `mechanism` and `open_risks` are optional; the note carries the argument. A
      required field is pressure to write the note as a rendering of the fields. The
      verdict skips a blank mechanism rather than leaving a gap.
- [x] 16.4 The prompt's "a hypothesis has four parts" list — the actual source of the
      four-part post — is reframed as properties of the thinking, with an explicit
      note that it is not a shape to pour a post into, and per-situation guidance
      (opening / answering / conceding / changing your mind / stuck).
- [x] 16.5 A later round is told it is replying, with the round number, and told not to
      restate what is not in dispute. Told "propose a hypothesis" every turn, a model
      re-emits the whole hypothesis with the objection folded in — the half of the
      problem a renderer change cannot fix.
- [x] 16.6 Pinned by test at both levels: `to_post_body` equals the note and adds no
      scaffold, and the body that reaches the forum end to end is the role's own text.
      Mutation-verified by restoring the old template.
- [x] 16.7 The lede says the lab is externally hosted and that outside experts and
      their agents can be invited — but only when `/forum/status` reports the forum
      actually shared. On a local-only deployment it says so instead, because naming a
      capability nobody outside the machine can reach is worse than naming none.
      `forum === null` is treated as not-yet-known rather than not-shared: keyed off
      `forum?.shared` alone, a shared lab flashed "on this machine only" on first paint.

## 17. Attachments

- [x] 17.1 `Grounding.uploads` wired from the run's project agent, so
      `read_attached_paper` is granted. Distinct source from the knowledge base: that
      is indexed literature the debate queries, these are the specific files a human
      put in front of this piece of work. All three roles get it — the Referee too,
      since the human's own evidence is part of the record it rules on.
- [x] 17.2 The filename is untrusted (the model supplies it), so it goes through the
      files service's own `_get_file` traversal check rather than a second copy of
      that logic. Tested through the real reader, not against the validator in
      isolation: the property that matters is that this path *calls* the check.
      Mutation-verified — removing it lets `../secret.txt` through.
- [x] 17.3 PDFs extracted with PyMuPDF, already a backend dependency for the KB
      indexer. `get_text` is polymorphic across modes, so the mode is named and the
      result type-checked; a cast would have turned a structured page into the repr
      of a list. An empty extraction says it may be a scan rather than returning "".
- [x] 17.4 Non-text attachments are described, not decoded — bytes of a spreadsheet
      waste a turn and can look enough like prose to be reasoned about. Long ones are
      truncated at 60k characters with the truncation marked, because a silently
      shortened paper is one a role will treat as though it read the conclusions.
- [x] 17.5 Calling with no name lists what is attached: a role cannot ask for a file
      it does not know exists.

## 18. Waiting, and saying so

- [x] 18.1 A role waits for the simulation it commissioned and gets the output inside
      its turn, so it can argue from the number. Reverses 10.2's non-blocking design:
      that was true to the shapes involved — a job outlives a round — but the result
      arrived after the verdict, where no agent read it. Bounded by
      `forum.max_job_wait` (30 min default); on timeout the debate argues on and the
      result still reaches the thread.
- [x] 18.2 A timeout is reported as "still running", never as failure. A role told a
      job failed when it merely has not finished argues from a false negative.
- [x] 18.3 **The deadlock waiting would otherwise cause.** A waiting role waits for
      the monitor to record a result; an open write transaction on SQLite stops the
      monitor writing it. Three changes: the commissioner uses its own committed
      session, `wait_for_result` opens one session per poll, and the orchestrator
      commits before each role speaks (`_settle`). All three mutation-verified —
      holding one session across the wait makes the wait time out, and dropping the
      pre-turn commit makes another session see nothing mid-turn.
- [x] 18.4 The commissioner's sessions come from the *caller's* engine, not
      `get_engine()`. Reaching for the process-wide engine had the debate writing to a
      different database than the caller was reading, which is what it did under test.
- [x] 18.5 `DebateRunTable.activity` / `activity_since`, emitted on the event stream
      and rendered as a pulsing line with an elapsed count. A turn produces nothing
      until it finishes, so a thread that stops growing looks identical to a crash —
      and with waiting that silence is now minutes. Cleared in `finally`, because a
      stale line is the frozen screen again with a caption claiming otherwise.
- [x] 18.6 The wait announces what it is waiting for, job id and cluster included, from
      its own session — a tool has no handle on the session running the debate.
- [x] 18.7 `_wired` and the commissioner tests commit rather than flush. They passed
      only because everything shared one session; the commissioner now needs the run
      committed, which `open_debate` does in production.

## 19. Why the Reviewer blocked in every round

- [x] 19.1 Root cause: `prior_debates` filtered threads on `status == "closed"`, but a
      concluded VISTA debate posts DONE and leaves its thread *open* — h5i then reports
      it `done`. So the tool answered "no match" on a forum with five finished debates,
      the Reviewer rephrased its way through a dead end that returns identical output
      for every phrasing, and each attempt cost a request. Four rounds, four BLOCKED
      notes, no critique. Contract §6.1.
- [x] 19.2 Ruled out by measurement rather than assumed: output-validation retries cost
      2 requests and raise `UnexpectedModelBehavior`, not `UsageLimitExceeded`; and the
      Reviewer failed both with four tools and with only `prior_debates`, so tool count
      was not it either.
- [x] 19.3 **The fake could not express `done`.** It emitted open/closed only, which is
      why a bug hinging on `done` survived every test. It now derives status from the
      last content post the way v0.3.8 does, and hides only `closed` without `--all`.
- [x] 19.4 A no-match answer now lists the finished thread titles and says rephrasing
      will not help. A tool that returns the same refusal to every phrasing invites
      being called again, and each call is a request.
- [x] 19.5 `prior_debates` excludes the debate's own thread, so a role does not read its
      own half-finished argument back as settled precedent.
- [x] 19.6 A role that exhausts its budget gets one more turn with its tools withheld.
      It cannot loop without tools, and an argument from the thread beats a BLOCKED note
      that tells the debate nothing.
- [x] 19.7 A BLOCKED note records the tool calls the turn made — the one post where
      provenance was empty and the one where a reader most needs it. The first attempt's
      calls survive the retry rather than being cleared: they are what the budget went on.

## 20. Nothing was polling the jobs

- [x] 20.1 Root cause of five jobs sitting at `submitted` for three days:
      `campaigns.monitor_enabled` defaults to **False**, and nothing in the
      deployment's `.env` turned it on. The campaign monitor is the only thing that
      polls a cluster or collects a result, so no `post_result` ever ran and no FINDING
      was ever posted. Enabled, with the interval lowered to 60s since a debate now
      waits on the answer.
- [x] 20.2 §18's waiting was built on a poller nobody had checked was running — its own
      comment asserted the latency floor was the monitor interval, which presumed it
      ran. With the monitor off the wait could only ever time out, stalling a round for
      the full budget per job.
- [x] 20.3 The commissioner refuses to wait when the monitor is disabled, and says so:
      `JobOutcome.uncollectable`, distinct from `timed_out`. The old message —
      "the result will be posted to this thread when it finishes" — was a promise the
      deployment could not keep, written into the permanent record of the debate.
- [x] 20.4 `max_job_wait` renamed `max_job_wait_seconds`. It had been set to `30`
      meaning half an hour and was half a minute. Minutes is the natural unit for a
      cluster job and seconds is what the code wants, so the unit belongs in the name.
- [x] 20.5 `test_campaign_settings_default_off` asserted on the process-wide `settings`,
      which reads the developer's `.env` — so it passed or failed on a file outside the
      repository, and reported a broken default the moment a deployment legitimately
      enabled the monitor. It now builds a fresh `CampaignSettings`.

## 21. The monitor could not poll a debate job

- [x] 21.1 With the monitor finally running, every debate job failed its first poll:
      `Campaign … has no session_id; cannot resolve its sandbox volume`. A debate
      campaign has no chat session by design — its result goes to a forum thread —
      and `CampaignMonitor._is_orphaned` says so in as many words and exempts it. The
      exemption was written there and not in `_job_run_user_paths`, which the poll path
      goes through.
- [x] 21.2 The guard was also vacuous as written: it refuses over a sandbox volume that
      the next line resolves from `project_paths_for(project_id, user_id)`, which never
      uses a session. Exemption mirrored rather than the guard deleted, so the case it
      was written for — a chat campaign whose conversation was deleted — is still
      refused. Both directions pinned by test and mutation-verified.
- [x] 21.3 Corrected the comment in `agent_tools.start_campaign` that claimed the
      session id keys the sandbox volume. That belief is what produced 21.1.
- [x] 21.4 Verified against the four real stuck jobs: all four now resolve their poll
      wiring, and all four commissioning identities are still attached in h5i's roster —
      so their results will reach the right threads under the right names. The roster
      survived because `_retire` keeps one attached while its jobs are in flight.

## 22. A malformed answer killed the debate

- [x] 22.1 `TURN_FAILED` groups the ways a role fails to produce a usable answer —
      a spent request budget, and the model returning a shape that will not validate
      (`UnexpectedModelBehavior`, `ToolRetryError`). All three cost a round. Only the
      budget case was caught, so a prose answer escaped `run_round`, escaped `run`, and
      marked the whole run `failed` with nothing in the thread to say why. Deliberately
      not a bare `except Exception`: a forum that has gone away or a bug in this module
      is a fault, and swallowing it into a BLOCKED note would make every one of them a
      debate that quietly argued worse.
- [x] 22.2 The BLOCKED note names which failure it was. "This is a budget limit" was
      written when a budget was the only thing caught and became a lie for a model that
      had not run out of anything.
- [x] 22.3 Roles use `PromptedOutput`, not tool-based output. pydantic-ai's default asks
      the model to answer by calling a synthetic `final_result` tool; `gpt-oss-120b`
      answered in prose, which was then parsed as JSON and failed at "line 1 column 1" —
      losing a reply that had engaged both objections and revised the hypothesis with
      numbers. Prompted output asks for JSON in the prompt and parses it out of the
      text, so a text answer is the expected shape. Ordinary tools are unaffected.
- [x] 22.4 The fakes emit JSON as text rather than an output tool call, so they model
      the path production takes. The first pin of 22.3 was silent under mutation —
      pydantic-ai's tool mode also accepts valid JSON text — so it is pinned instead on
      `AgentInfo.output_tools`, which is `['final_result']` under tool output and empty
      under prompted output.

## 23. The default cluster was chosen alphabetically

- [x] 23.1 Root cause of "I asked for a simulation and nothing launched": the default
      cluster was `sorted(user_credentials)[0]`, chosen with no reference to the job.
      The opener could reach frontier, odo and perlmutter, so the default was
      *frontier* — the one cluster `salt-neutronics-tbr` has no section for. Two
      attempts, both refused by `submit_hpc_job`, nothing submitted. For
      `salt-chemistry-md`, which runs only on frontier, the same arbitrary default was
      right by luck; that is what made it look like it worked.
- [x] 23.2 `clusters_for_job` reads a job's own `cluster_defaults.json`, and
      `runnable_simulations` returns job → the clusters it can actually run on here —
      the intersection of the project's skills, the jobs on disk, and the opener's
      credentials. The default is now that job's first reachable cluster.
- [x] 23.3 A job with no reachable cluster is not offered at all, rather than offered
      and refused on every attempt. A NERSC-only opener is no longer shown a
      frontier-only job. Same rule as the ungranted web reader.
- [x] 23.4 `DebateDeps.runnable` replaces `available_jobs` + `available_clusters`. Two
      lists cannot say that one job runs on odo and perlmutter while another runs only
      on frontier, and the prompt's single joined list read as "any of these work for
      any of those" — which is how a role named a cluster its job could not use, was
      refused, and retried the identical call.
- [x] 23.5 The test catalogue writes a `cluster_defaults.json` per job with
      *different* clusters, as every real job has. Bare directories could not express
      the failure, which is why nothing caught it — the same gap as the fake's status
      vocabulary in §19.3.
- [x] 23.6 The budget was not consumed by the two refusals: `commission` creates the
      campaign row before submitting, and the failed submit rolls its session back.
      Verified against the live database — zero campaign rows for that debate.

## 24. The roster panel understated every debate's first stint

- [x] 24.1 Root cause of "the agent doesn't have the simulation tool": it did — the
      newest run's posts cite `search_literature`, `read_attached_paper` and three
      `commission_simulation` calls — but `debate_participant.granted_tools` read
      `["prior_debates", "read_domain_guidance"]`. `open_debate` builds an
      orchestrator to create the thread, and at that moment the run does not exist,
      so `build_run_grounding` — which needs it to find the project's knowledge bases
      and the opener's credentials — cannot have been called. The roster rows were
      written from that bare orchestrator and never revisited.
- [x] 24.2 `record_granted_tools` rewrites the active roster's grants at the top of
      `run()`, which is the only place that knows them: `start()` cannot, by
      construction. Retired stints are left alone — what an old stint could reach is a
      fact about the posts it made, and restating it in today's terms would show an
      old post as having had tools it never had.
- [x] 24.3 Both halves are pinned under mutation: removing the call fails the
      first-stint test, and widening it to every participant fails the retired-stint
      test.

## 25. A refusal only a human can clear was retried

- [x] 25.1 The same debate asked odo for the identical job twice and was told twice
      that its S3M token belongs to `chm243` while odo requires `gen150-vista`. Two of
      twelve requests spent on a fact already on the table — the same shape as the
      `prior_debates` dead end in §19: a tool that can only refuse invites being
      called again.
- [x] 25.2 `CommissionRefused` splits into `BadCommission` (wrong job or cluster; a
      corrected call works, and the reply now names what this debate *can* run) and
      `BudgetSpent` (final for the debate). Everything else out of a submission is the
      deployment — a token scoped to the wrong project, a scratch directory the
      submitter could not create — and the reply says plainly that no rephrasing will
      help.
- [x] 25.3 Environment refusals are memoised on `DebateDeps.refused_commissions`,
      keyed by (job, cluster), and a repeat is answered from memory without touching
      the cluster. Per pair and not per turn: a job odo refuses may well run on
      perlmutter, and blocking the turn would throw away the debate's other
      credential. `BadCommission` is deliberately not memoised.
- [x] 25.4 The key resolves `cluster=None` through the same rule the commissioner
      uses — the job's first reachable cluster — so omitting the argument is not a way
      to retry a dead credential. That also fixes the receipt, which used to record
      "the default cluster" where provenance needs a machine name.

## 26. A boot warning that named everything except the cause

- [x] 26.1 `ForumCommandError` took the *last* line of a failed command's output, on
      the theory that a CLI prints progress and then its error. Git does the opposite:
      the cause comes first and the advice paragraph follows. So a firewalled SSH port
      reported itself as `` `h5i forum sync` exited 1: and the repository exists`` —
      the tail of "Please make sure you have the correct access rights / and the
      repository exists", with `ERROR: Repository not found` four lines above it and
      thrown away. It read as a missing repo; the repo was there the whole time.
- [x] 26.2 `_collapse` joins every non-empty line with ` · ` and truncates at 400
      characters. Neither end is dropped, because either end carries the cause for
      some tool, and the full streams stay on the exception for anyone who wants the
      original shape. Pinned under mutation back to `lines[-1]`.
- [x] 26.3 `ensure_federation` puts the exception on the warning line rather than only
      in the traceback under it. A boot warning is read in a scrolling launch log,
      where the traceback is exactly the part that gets skipped — and "could not reach"
      alone does not separate a missing repo from a blocked port, which is the
      difference between creating a repo and changing one character of a URL.
- [x] 26.4 The deployment cause was environmental: github.com:22 is blocked on this
      network (`Connection timed out during banner exchange`), while HTTPS reaches the
      same repo and `gh` is authenticated over it with the osxkeychain helper. The
      forum remote is now the HTTPS URL, re-applied with `--branch-refs` so threads
      keep publishing where a ruleset can protect them.

## 26. A job was declared dead while its log showed it building a virtualenv

- [x] 26.1 Root cause of "the odo job completed but the agent said it failed": odo's
      IRI reported `FAILED` fifty-five seconds after submission — forty-five seconds
      after the same client had logged `Resource … is not UP (current status:
      unknown)` — most likely answering for a job it had not registered yet. The
      Slurm log fetched during that very poll is still on disk and ends
      `[salt-neutronics-tbr] env ready on odo03`, so the job was alive and setting up.
- [x] 26.2 A terminal state was final on one reading, and a terminal state also sets
      `result_collected`, so nothing ever looked again. A failure is now believed only
      when a second consecutive poll repeats it. Success is exempt: a scheduler does
      not report `COMPLETED` for a job it has not seen.
- [x] 26.3 The monitor called the collector only on success and wrote `{"state":
      "FAILED"}` on failure, discarding `raw_status` — which held the log that
      disproved the verdict. Both outcomes now go to the collector with an `ok` flag,
      and the campaign path keeps the status text on the step. A failed run's log is
      the most useful thing it produces: it separates "the physics says no" from "the
      script had a typo".
- [x] 26.4 A debate that lost a run now posts it. `post_result` had always rendered
      failures correctly — "did not complete", with the report attached — but was
      unreachable on that path, so the thread carried no sign a test had been tried.
- [x] 26.5 Four mutations pinned: no confirmation, confirmation demanded of successes
      too, failures withheld from the collector, and the campaign collector dropping
      the log. Fixing 26.2 also exposed a test of my own that passed for the wrong
      reason — `reconcile_once` swallows per-job exceptions, so an `AssertionError`
      raised inside an injected collector never reaches pytest. It asserts on a
      recorded flag now.

## 27. A live post said "based on model alone" until the debate ended

- [x] 27.1 Root cause: the event stream kept `seen: set[str]` and skipped any post
      whose id it had already emitted, so a post was final the moment it was sent.
      Only `body` is fixed once a post exists. Provenance is written a beat later by
      construction — `_post` publishes, reads the thread back to create the row, then
      calls `record_post_tools` — and the 10-second forum refresh the stream does for
      peer comments can create that row first from a record that has no field for how
      the agent got there. Any post caught in that window read "based on model alone"
      for the whole connection.
- [x] 27.2 The client was already right: it replaces on a matching `post_id` and its
      own comment says "a replayed post can still carry a newer vote tally". The
      server simply never replayed.
- [x] 27.3 The stream now keys on the serialized payload, so a post is re-emitted when
      anything on it moves and not otherwise. The payload *is* the fingerprint
      deliberately: a hand-maintained list of mutable fields is a list to forget to
      update, and forgetting means the next field added is invisible to live viewers.
      This also fixes vote tallies, `vouch_lane` and `authored_by`, which were stale
      live for the same reason.
- [x] 27.4 Three mutations pinned: remembering only the id, re-emitting on every poll,
      and a fingerprint that excludes `tools_used`. The second exposed a test of my
      own that was green on timing luck — it slept "a few poll intervals" and the
      fixture's interval is 10 ms, so the stream completed one pass before the run
      closed. It counts polls now.

## 28. Precedent cost twelve thousand tokens a lookup

- [x] 28.1 `_summarise_prior` matched on any single title word longer than three
      characters and quoted each match's whole verdict. On a forum that is about one
      subject every title says "FLiBe" or "TBR", so nearly everything matched.
      Measured against the live database: six finished threads, 49,098 characters of
      verdict, ~12,300 tokens for one call — plus six forum reads at ~1.5s each now
      that the forum has a remote.
- [x] 28.2 Matches are ranked by how many query words the title shares, then by
      recency, and the best `PRIOR_DEBATE_LIMIT` (3) are read. Ranking is what makes
      the cap safe: where every title shares one word, the count is the only thing
      separating the apt thread from the adjacent one.
- [x] 28.3 Each verdict is digested to its ranked claims, their confidence, and what
      it left unresolved. The `*Standing.*` narrative and `**Why this order.**`
      rationale are the argument behind the conclusions and 85% of the length, and a
      role reading precedent is not re-litigating the debate that produced it. A
      verdict that does not match our layout — a peer's, written by their agent — is
      clipped to `PRIOR_DIGEST_CHARS` rather than dropped.
- [x] 28.4 Net: ~12,300 tokens → ~905, and six forum reads → three. The dropped-match
      count is stated, not silent: a silent cap reads as "that is all there is", and
      the role would draw a conclusion from an absence we manufactured.
- [x] 28.5 Five mutations pinned. A sixth — deleting the `_verdict_digest` call from
      the lookup — survived at first: the digest was tested in isolation and every
      `_summarise_prior` test used a three-line verdict, where a digest is the whole
      thing. It is now asserted through the lookup's own answer on a verdict of
      realistic size, which is the only place the saving actually exists.

## 29. The agent invented flags, and the error was in a file nobody fetched

- [x] 29.1 Root cause of "the job produced no output": the agent's `script_args`.
      Job 44039 was launched with `--salt flibe_90Li6 --geometry arc_lib --multiplier
      beberyllide_nearwall_30cm --compare chloride_ternary_natCl`; 44018 with
      `--composition-sweep --bef2-mol-pct 10,20,33,…`. `run_state_point.py` accepts
      `--skill-root`, `--output-dir`, `--bef2`, `--be-multiplier`, `--li6`,
      `--nominal-bef2`, `--allow-extrapolation`. argparse exits 2 on an unknown
      option — which is exactly the `EXIT_CODE=2` both jobs reported.
- [x] 29.2 The invocation is now in the situation. `usage_for_job` reads the flag
      lines out of `hpc_jobs/<job>/README.md` — which already documents the options
      and gives a worked `script_args=` example — and `_situation` renders them under
      each runnable job. It was never shown to a debating role before:
      `read_domain_guidance` serves the *skill* body, and for this skill that
      documents `python -m salt_neutronics.cli`, a different interface from the HPC
      wrapper's.
- [x] 29.3 Extraction is "every line mentioning a long option", not a section parse:
      a heading convention is a thing for the next README to get subtly wrong, and
      the few neighbouring lines it also catches (a range caveat, a "you MUST pass
      `--model`" warning) are worth having. Capped at `USAGE_CHARS`.
- [x] 29.4 The tool docstring told it the wrong job name. "name the `hpc_jobs/<name>`
      that would test it" is why every commissioning turn opened with
      `job="hpc_jobs/salt-neutronics-tbr"`, was refused, and retried — one request
      out of twelve, every turn, spent on an error we authored.
- [x] 29.5 stderr is fetched and shown. Every cluster spec has always set
      `stdout_path` **and** `stderr_path`; only stdout was recorded, so only stdout
      was fetched, so a job that died with a usage message or a traceback looked
      silent. `SubmittedJob` carries `err_path`, both files ride one Globus task (the
      overhead is per task, not per file), and the status text has a `--- STDERR ---`
      section. A failed fetch says so rather than rendering as an absence.
- [x] 29.6 `submit_hpc_job` now returns the rendered `log_path`, `err_path` and
      `output_dir`, and `parse_submit_summary` keeps them. They existed only in the
      MCP server's own registry, so Vista's job rows were blank and the report
      attached to a debate's FINDING post named no file a human could open.
- [x] 29.7 Nine mutations pinned. Two survived first: dropping the digest… no —
      dropping the stderr fetch from the *OLCF* path, and collapsing a failed fetch
      into an absence. The OLCF status path had no test at all, because
      `FakeGlobusClient` recorded transfers without writing files — fine while every
      transfer went outward, useless for a path that pulls logs in and reads them off
      disk. The fake now lands whatever a test seeds. Same lesson as the fake h5i
      status vocabulary (§19.3) and the bare job catalogue (§23.5).
