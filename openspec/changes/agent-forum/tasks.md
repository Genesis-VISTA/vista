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

- [ ] 7.1 Per-role tool grants: RAG/KBs, salt-chemistry + neutronics skills
- [ ] 7.2 Prior closed threads on the same forum as readable context
- [ ] 7.3 User-uploaded papers via the existing uploads/files service
- [ ] 7.4 `h5i browser` reads under an egress allowlist; receipt attached to the citing post
- [ ] 7.5 Assert a refused fetch stays visible in the record

## 8. API (MR 6)

- [ ] 8.1 `api/debate.py` at `/projects/{name}/debates` — create, list, get, close
- [ ] 8.2 Human-post endpoint (host-side post, attributed `human`)
- [ ] 8.3 SSE stream for live posts
- [ ] 8.4 Register router in `api/api.py`; project membership as the access boundary
- [ ] 8.5 Route tests

## 9. UI (MR 7)

- [ ] 9.1 Read `ui/node_modules/next/dist/docs/` before writing any Next.js (AGENTS.md)
- [ ] 9.2 Debate thread view rendering the host-stamped / agent-claimed boundary
- [ ] 9.3 Role badges, kind chips, vote tallies, attachment links
- [ ] 9.4 Live SSE + human post box + close control
- [ ] 9.5 Verdict panel for the Referee's ranked hypothesis
- [ ] 9.6 Vitest for new `ui/lib` helpers; `npm run lint` clean

## 10. HPC in the loop (MR 8 — separable)

- [ ] 10.1 Referee/Reviewer may commission a campaign run to test a prediction
- [ ] 10.2 Finished job posts back as `FINDING` with a `test-report` attachment
- [ ] 10.3 Marked `hpc`; excluded from the default PR filter

## 11. Close out

- [ ] 11.1 `README.md` + `AGENTS.md` sections for the forum feature
- [ ] 11.2 `./scripts/ci-local.sh` green across backend / ui / mcp
- [ ] 11.3 `openspec` sync + archive
