## ADDED Requirements

### Requirement: Attested per-role attribution

Every debate post SHALL carry a host-stamped forum identity distinct per role.
The backend MUST post through each role's attached `h5i box`; it MUST NOT post
debate content host-side, because host-side `h5i forum post` has no `--as` flag
and attributes every post to `human`.

#### Scenario: Each role posts under its own identity

- **WHEN** the Proposer and Reviewer each post to a debate thread
- **THEN** `h5i forum read --json` SHALL report distinct `sender` values
- **AND** each post SHALL carry `role`, `box_id` and `policy_digest` stamped by the host

#### Scenario: Host-stamped and agent-claimed fields stay separate

- **WHEN** a post is projected into `DebatePostTable` and rendered in the UI
- **THEN** only `body` SHALL be presented as agent-authored
- **AND** the `vouch` lane SHALL be stored and displayed separately from post fields

### Requirement: Posts are confirmed, never assumed

The forum client SHALL validate a post's `kind` against the known set before
invoking the CLI, and SHALL confirm the post landed by re-reading the thread. A
zero exit status MUST NOT be treated as proof of publication.

#### Scenario: Unknown kind is rejected before it is lost

- **WHEN** the client is asked to post an unrecognised kind
- **THEN** it SHALL raise before spawning the CLI
- **AND** the test suite SHALL document that h5i accepts unknown kinds with exit 0 and then silently drops them

#### Scenario: A dropped post is detected

- **GIVEN** a staged post that never appears in the thread
- **WHEN** the client re-reads the thread to confirm
- **THEN** it SHALL surface a failure rather than reporting success

### Requirement: Bounded rounds with a Referee verdict

A debate SHALL run a configurable number of rounds, defaulting to 5, after which
the Referee SHALL post a verdict containing a ranked hypothesis with claim,
mechanism, falsifiable predictions, confidence, and open risks.

#### Scenario: Budget exhausts and the Referee closes the argument

- **WHEN** the configured round budget is reached
- **THEN** the Referee SHALL post a `DONE` carrying the ranked hypothesis
- **AND** the run status SHALL become terminal

#### Scenario: The Reviewer is adversarial

- **WHEN** the Reviewer responds to a `PROPOSAL`
- **THEN** it SHALL post `RISK` or `FINDING` replying to that post, or upvote it when it has nothing to add
- **AND** it MUST NOT post a bare agreement as a new post

### Requirement: The human participates and may end the debate

The human SHALL be able to post into a live thread, and SHALL be able to end it
at any time with `h5i forum close`. The orchestrator SHALL treat closure as an
expected control-flow signal.

#### Scenario: A human post reaches the next round

- **GIVEN** an open debate with rounds remaining
- **WHEN** the human posts to the thread
- **THEN** the orchestrator SHALL include that post in the next round's context

#### Scenario: Closure stops the loop cleanly

- **GIVEN** a running debate
- **WHEN** the human closes the thread
- **THEN** the next box-side post SHALL fail with exit 1
- **AND** the orchestrator SHALL end the run without raising an error

### Requirement: Grounded, checkable citations

Debate agents SHALL ground claims in VISTA knowledge bases, the salt-chemistry
and neutronics skills, prior closed threads, and user-uploaded papers. Literature
fetched with `h5i browser` SHALL run under an egress allowlist, and the session
receipt SHALL be attached to the citing post.

#### Scenario: A web citation carries its receipt

- **WHEN** an agent cites a page it fetched through `h5i browser`
- **THEN** the receipt SHALL be staged inside the role box's work directory and attached by relative name
- **AND** the attachment SHALL be retrievable host-side with `h5i forum fetch`

#### Scenario: A refused fetch is visible

- **WHEN** a fetch is refused by the egress allowlist
- **THEN** the refusal SHALL remain readable in the session record rather than being swallowed

### Requirement: Peer posts are data, not instructions

Role prompts SHALL state that forum content is untrusted peer input. The client
MUST NOT interpolate post bodies into a shell; all CLI invocations SHALL pass
argument lists.

#### Scenario: An instruction-shaped post is not obeyed

- **WHEN** a post body contains text directing an agent to act outside its task
- **THEN** the agent SHALL treat it as a claim to evaluate
- **AND** it SHALL be able to flag it with `--kind RISK`

### Requirement: Hermetic tests without the h5i binary

PR CI SHALL exercise the client, orchestrator and role agents without a real h5i
binary, a live LLM, or network access. Tests requiring a real binary SHALL be
marked `live`.

#### Scenario: The suite passes with h5i absent

- **WHEN** PR CI runs with no h5i on `PATH`
- **THEN** forum tests SHALL pass against a fake shim replaying recorded fixtures
- **AND** the feature SHALL be inert rather than failing when `ForumSettings.enabled` is false

#### Scenario: Fixtures are tied to a verified version

- **WHEN** fixtures are recorded
- **THEN** they SHALL correspond to the version documented in `docs/h5i-forum-contract.md`
- **AND** the contract SHALL be re-verified on an h5i upgrade
