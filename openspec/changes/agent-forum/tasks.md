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
- [ ] 7.3 User-uploaded papers via the existing uploads/files service — **not wired**.
      The tool and its fencing exist; nothing sets `Grounding.uploads`, so
      `read_attached_paper` is never granted. Same failure as 7.1 and not yet fixed.
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
