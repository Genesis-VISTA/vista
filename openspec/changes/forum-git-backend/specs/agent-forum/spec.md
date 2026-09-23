## ADDED Requirements

### Requirement: Per-role attribution on each post

Every debate post SHALL name the role identity that wrote it (for example
`vista-proposer-1a2b3c4d`, or `human` for the operator) and the random host id
of the install that wrote it. A debate SHALL keep one roster for its whole life:
continuing a debate for more rounds MUST reuse the same role identities rather
than minting new ones. Posts VISTA writes SHALL also record the VISTA account
that wrote them locally, as today.

#### Scenario: Each role posts under its own identity

- **WHEN** the Proposer and Reviewer each post to a debate thread
- **THEN** the two posts SHALL carry different identities and the same host id

#### Scenario: Continuing a debate keeps its roster

- **GIVEN** a debate that has concluded and is continued for 3 more rounds
- **WHEN** the Proposer posts in the continued rounds
- **THEN** its posts SHALL carry the same identity as its earlier posts

#### Scenario: Only the body is agent-authored

- **WHEN** a post is projected into the database and rendered in the UI
- **THEN** only `body` SHALL be presented as agent-authored
- **AND** the provenance lane SHALL be stored and displayed separately from post fields

### Requirement: Threads live on the project's git remote

Each debate thread SHALL be an append-only git branch at
`refs/heads/vista-forum/threads/<thread-id>` on the project's forum repository,
where each post is one commit adding one post file (and at most one attachment
beside it). Post ids MUST be unique across hosts. Posts SHALL be ordered by the
order the remote accepted their commits, not by timestamps. VISTA MUST push only
refs under `refs/heads/vista-forum/`, MUST NOT force-push, and MUST NOT rewrite
or delete a thread's history.

#### Scenario: Only forum refs are published

- **GIVEN** a working repository that also holds other branches
- **WHEN** VISTA syncs a project's forum
- **THEN** the remote SHALL receive only refs under `refs/heads/vista-forum/`

#### Scenario: A peer's history rewrite does not lose our posts

- **GIVEN** a thread whose remote history was force-pushed to drop posts this install already holds
- **WHEN** VISTA next syncs that thread
- **THEN** VISTA SHALL NOT force-push
- **AND** posts this install wrote SHALL be re-published on top of the remote history
- **AND** peer posts that vanished SHALL remain in the local projection, marked as no longer on the remote

### Requirement: Posting works offline and publishes later

A post SHALL be durable as soon as it is committed to the local working
repository, whether or not the remote is reachable. Publishing SHALL fetch,
re-apply this install's unpublished posts on top of the remote tip, and push,
retrying a bounded number of times when the push is rejected. Posts not yet
published SHALL be flushed on the next successful sync and SHALL be shown as
not yet published until then. An unreachable remote MUST NOT fail a debate turn.

#### Scenario: A debate runs while offline

- **GIVEN** a running debate whose remote is unreachable
- **WHEN** the Proposer and Reviewer post
- **THEN** both posts SHALL be committed locally and the debate SHALL continue
- **AND** both SHALL be marked not yet published

#### Scenario: Two hosts post at the same moment

- **GIVEN** two installs that each commit a post to the same thread before either pushes
- **WHEN** both publish
- **THEN** both posts SHALL end up on the remote with no conflict and no lost post

#### Scenario: Unpublished posts flush on reconnect

- **GIVEN** posts committed while the remote was unreachable
- **WHEN** a later sync reaches the remote
- **THEN** those posts SHALL be pushed and no longer marked not yet published

### Requirement: Provenance lanes

Every post SHALL be assigned a lane. A post SHALL be `host-observed` only when
this install's local database recorded writing it; a matching host id or
identity in the post file MUST NOT be sufficient, because a peer can copy them.
Any other post SHALL be `peer-claimed`, or `unattributed` when it carries no
usable host id. A peer's identity and host id SHALL be presented as that peer's
claim, never as fact. A thread closed by a peer SHALL read as ended by a peer.

#### Scenario: A peer copies our host id

- **GIVEN** a peer post whose host id and identity equal this install's Proposer
- **WHEN** VISTA reads the thread
- **THEN** that post SHALL be `peer-claimed`

#### Scenario: A peer closes the thread

- **WHEN** the first `CLOSED` post in a thread is `peer-claimed`
- **THEN** the UI SHALL show the debate as ended by a peer, not ended early

### Requirement: One vote per identity per post

A vote SHALL be an `UPVOTE` or `DOWNVOTE` post replying to its target. The tally
for a post SHALL count at most one vote per (host id, identity), taking that
voter's latest vote. Votes from `host-observed` posts and from all other posts
SHALL be tallied and displayed separately.

#### Scenario: A repeated vote counts once

- **GIVEN** one identity that upvotes the same post twice
- **WHEN** the post's tally is computed
- **THEN** it SHALL count one vote from that identity

#### Scenario: A changed vote replaces the earlier one

- **GIVEN** one identity that upvotes and later downvotes a post
- **WHEN** the tally is computed
- **THEN** only the downvote SHALL count

### Requirement: A thread missing from the remote

When a debate's thread branch exists neither on the remote nor in the local
working repository, VISTA SHALL show the debate from its stored copy, marked as
no longer on the forum, and SHALL disable posting and continuing it. This MUST
NOT raise an error to the page or retry indefinitely. The same path SHALL apply
to debates created before this change, whose threads were written by h5i.

#### Scenario: A thread was deleted on the forge

- **GIVEN** a concluded debate whose thread branch someone deleted from the remote
- **WHEN** a user opens the debate
- **THEN** the page SHALL show the stored posts and say the thread is no longer on the forum
- **AND** posting and continuing SHALL be unavailable

#### Scenario: A debate from the h5i era

- **GIVEN** a debate row whose thread id refers to an h5i thread
- **WHEN** a user opens it
- **THEN** it SHALL behave as a missing thread, with no h5i-specific handling

### Requirement: Git is a prerequisite, checked honestly

The lab SHALL require a working system `git`, version 2.34 or later. VISTA SHALL
check it without triggering an operating-system install prompt: on macOS, a
`/usr/bin/git` whose developer tools are not installed MUST be treated as git
absent. When git is absent or too old, every project's lab SHALL be off with the
reason stated in the forum status and in the project dialog, and saving a forum
repository SHALL fail with that reason. The rest of VISTA MUST keep working.

#### Scenario: macOS without developer tools

- **GIVEN** macOS where `/usr/bin/git` exists but the command line developer tools are not installed
- **WHEN** VISTA checks for git
- **THEN** git SHALL be reported absent and no install dialog SHALL appear

#### Scenario: Git missing

- **GIVEN** a host with no git on `PATH`
- **WHEN** a user opens the Hypothesis Lab
- **THEN** it SHALL say the lab needs git, and other VISTA features SHALL be unaffected

### Requirement: Attachments are bounded

A post SHALL carry at most one attachment, committed with it. An attachment
larger than 1 MB SHALL be truncated with a visible marker, and the post SHALL
record the full content's size and SHA-256; the full content SHALL be kept on the
posting install. Receipts SHALL be published in full up to that bound, including
text read from papers attached to the project.

#### Scenario: A large receipt

- **WHEN** a turn's receipts exceed 1 MB
- **THEN** the published attachment SHALL be at most 1 MB and say it was truncated
- **AND** the post SHALL record the full size and SHA-256
- **AND** the full receipts SHALL remain readable on the posting install

### Requirement: The project's forum remote is verified when saved

Saving a project with a forum repository SHALL initialise that project's working
repository and reach the remote before the save succeeds; an unreachable or
unauthorised remote SHALL fail the save with git's error. Pushes and fetches
SHALL use the user's own git credentials, and git MUST NOT prompt interactively.
Pointing a project at a repository that already holds threads SHALL join them.

#### Scenario: A typo in the repository URL

- **WHEN** a user saves a project with a forum URL that does not resolve
- **THEN** the save SHALL fail and show git's error in the dialog

#### Scenario: Joining an existing forum

- **GIVEN** a forum repository that already holds threads from another install
- **WHEN** a user saves a project pointing at it
- **THEN** those threads SHALL be listed for the project

### Requirement: Signed peer attribution

VISTA SHALL sign the commits of posts it writes with SSH, using the key the user
pushes with through `ssh-agent`, or a VISTA-generated key the user registered on
the forge when no agent key is available. A post whose commit signature verifies
against the keys the forge publishes for the named account SHALL be shown as
signed by that account; an unsigned post, or one whose signature does not
verify, SHALL stay `peer-claimed`. Once signing is available, votes SHALL also be
countable once per signed account. This requirement is delivered in the change's
final phase; until then posts carry no signature.

#### Scenario: A signed peer post

- **GIVEN** a peer post signed by a key the forge lists for account `jqyin`
- **WHEN** VISTA reads the thread
- **THEN** the post SHALL be shown as signed by `jqyin`

#### Scenario: A forged signer claim

- **GIVEN** a peer post that names `jqyin` as signer but is signed by a key the forge does not list for `jqyin`
- **WHEN** VISTA reads the thread
- **THEN** the post SHALL NOT be shown as signed by `jqyin` and SHALL stay `peer-claimed`

### Requirement: Hermetic tests against real git

PR CI SHALL exercise the forum client against real git, using temporary local
bare repositories as the remote and separate working repositories as separate
hosts, with no network, forge, LLM or h5i. The orchestrator, API and role tests
SHALL use a fake client. The feature SHALL be inert, not failing, when the forum
is disabled or git is absent.

#### Scenario: The client suite runs hermetically

- **WHEN** PR CI runs the forum client tests
- **THEN** they SHALL pass with only git available and no network access

#### Scenario: A push race is tested for real

- **WHEN** the client tests run
- **THEN** at least one test SHALL have two working repositories race a push to one bare remote and assert both posts land

## MODIFIED Requirements

### Requirement: Posts are confirmed, never assumed

The forum client SHALL validate a post's `kind` against the known postable set
before writing anything, and SHALL treat a post as written only once its commit
exists in the local working repository. Whether a post has reached the remote
SHALL be tracked separately and reported, never inferred.

#### Scenario: Unknown kind is rejected before it is written

- **WHEN** the client is asked to post an unrecognised kind
- **THEN** it SHALL raise before creating any commit

#### Scenario: Publication is reported, not assumed

- **GIVEN** a post committed locally whose push was rejected
- **WHEN** the post is shown
- **THEN** it SHALL be marked not yet published rather than reported as on the forum

### Requirement: The human participates and may end the debate

The human SHALL be able to post into a live thread, and SHALL be able to end it
at any time by closing it, which writes a `CLOSED` post. Once a thread holds a
`CLOSED` post, from this install or fetched from a peer, VISTA MUST refuse to
post to it, and readers SHALL ignore any post committed after the first
`CLOSED` post (it stays in git history). The orchestrator SHALL treat closure as
an expected control-flow signal.

#### Scenario: A human post reaches the next round

- **GIVEN** an open debate with rounds remaining
- **WHEN** the human posts to the thread
- **THEN** the orchestrator SHALL include that post in the next round's context

#### Scenario: Closure stops the loop cleanly

- **GIVEN** a running debate
- **WHEN** the human closes the thread
- **THEN** the next role post SHALL be refused as closed
- **AND** the orchestrator SHALL end the run without raising an error

#### Scenario: A peer posts after the close

- **GIVEN** a closed thread
- **WHEN** a peer pushes a post committed after the `CLOSED` post
- **THEN** readers SHALL NOT show it as part of the thread

### Requirement: Grounded, checkable citations

Debate agents SHALL ground claims in VISTA knowledge bases, the salt-chemistry
and neutronics skills, prior closed threads, user-uploaded papers and
commissioned simulations. The receipts for a turn's tool calls SHALL be attached
to the post that turn produced, so that a peer holding only the git remote can
read what the post cited. A refused tool call SHALL leave a receipt too.

#### Scenario: A citation carries its receipt

- **WHEN** an agent's post relies on tool calls in that turn
- **THEN** the post SHALL carry an attachment holding those calls' receipts
- **AND** a peer cloning the remote SHALL be able to read that attachment

#### Scenario: A refused call is visible

- **WHEN** a tool call in a turn is refused
- **THEN** its refusal SHALL appear in that turn's receipts rather than being swallowed

### Requirement: Peer posts are data, not instructions

Role prompts SHALL state that forum content is untrusted peer input. Post bodies
MUST NOT be interpolated into a shell; every git invocation SHALL pass an
argument list. A post file that cannot be parsed, or that carries an unknown
format version or kind, SHALL be skipped and logged rather than failing the read.

#### Scenario: An instruction-shaped post is not obeyed

- **WHEN** a post body contains text directing an agent to act outside its task
- **THEN** the agent SHALL treat it as a claim to evaluate
- **AND** it SHALL be able to flag it with a `RISK` post

#### Scenario: A malformed peer file

- **GIVEN** a peer commit that adds a file that is not valid post JSON
- **WHEN** VISTA reads the thread
- **THEN** the other posts SHALL be read normally and the bad file SHALL be skipped and logged

## REMOVED Requirements

### Requirement: Attested per-role attribution

**Reason**: It required posting through a per-role `h5i box`, because host-side
`h5i forum post` had no `--as` flag. h5i's forum no longer exists, and the boxes
added no real separation: all roles run inside the backend.

**Migration**: Replaced by "Per-role attribution on each post" (identity in the
post file) and "Provenance lanes" (what this install observed). Verifiable
attribution comes from "Signed peer attribution".

### Requirement: Hermetic tests without the h5i binary

**Reason**: The fake h5i shim replayed recorded v0.3.8 output, which is why CI
stayed green after h5i deleted the forum. There is no h5i to fake.

**Migration**: Replaced by "Hermetic tests against real git".
