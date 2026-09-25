## Why

A scientist gives VISTA a topic and gets one model's answer, shaped by whatever
that single context happened to retrieve. Nothing argues the other side, nothing
is forced to try to falsify the claim, and the reasoning behind the answer is not
recoverable afterwards.

Adversarial debate between specialised agents produces better hypotheses than one
agent reasoning alone — but only if the disagreement is real and the record is
readable. That needs durable per-role attribution, which VISTA has no substrate
for today.

h5i (vendored in `h5i/`) already is that substrate: a git-backed forum where the
**host** stamps sender, role, box and policy digest, and the agent owns only the
message body. VISTA supplies the science; h5i supplies the attested record.

## What Changes

- A `debate` capability: a human opens a thread on a topic; Proposer, Reviewer and
  Referee argue it for a bounded number of rounds; the Referee posts a ranked
  hypothesis with falsifiable predictions and open risks.
- `services/h5i_forum.py` — a typed async client over the `h5i forum` CLI,
  written against the verified contract in `docs/h5i-forum-contract.md`.
- Each role is attached to the forum through its own `h5i box`, used as a post
  relay. Agents stay PydanticAI in-backend and keep the full VISTA tool stack;
  only the `forum post` call executes inside the role's box, because host-side
  posting has no identity and would attribute every role to `human`.
- Debate state projected into `DebateRun` / `DebateParticipant` / `DebatePost`
  tables for UI reads. The forum's git store stays source of truth.
- Grounding: VISTA RAG / knowledge bases, the salt-chemistry and neutronics
  skills, prior closed threads, user-uploaded papers, and `h5i browser` reads
  under an egress allowlist with the session receipt attached as evidence.
- The human may post into a live thread and may end it early with
  `h5i forum close`; h5i enforces the stop.
- REST + SSE under `/projects/{name}/debates`, and a UI thread view that renders
  the host-stamped / agent-claimed boundary rather than flattening it.

## Capabilities

### New Capabilities

- `agent-forum`: multi-agent scientific debate on an attested h5i forum thread

### Modified Capabilities

- (none)

## Non-goals

- **No VISTAGuard work.** Peer-post-as-untrusted-input is handled in role prompts
  and the client; G1–G7 gates are explicitly out of scope.
- **Not fully boxed agents.** Roles relay posts through boxes but run in the
  backend. The backend is host *and* agent runtime, so a compromised backend
  could forge any role. Recorded as a limitation, not solved here.
- No cross-machine forum (`forum remote` / `enroll` / `sync`), no `container`
  or `microvm` tier (unavailable on the dev host), no Playwright E2E.
- HPC-in-the-loop is scoped but sequenced last and may ship separately.

## Impact

- `backend/src/vista_backend/`: `services/h5i_forum.py`, `services/debate.py`,
  `agents/forum/`, `api/debate.py`, `db/schemas.py`, `config.py`
- `ui/`: debate thread page + API route
- `docs/h5i-forum-contract.md` (landed), `README.md`, `AGENTS.md`
- New hermetic test suites; h5i binary is faked in PR CI and marked `live` when real
