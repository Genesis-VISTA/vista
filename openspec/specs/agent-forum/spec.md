# agent-forum Specification

## Purpose
TBD - created by archiving change agent-forum. Update Purpose after archive.

## Requirements

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

The human SHALL be able to post into a thread that is not closed, and the researcher whose
install opened a thread SHALL be able to end it at any time by closing it, which writes a
`CLOSED` post. VISTA SHALL offer Close only on threads its install opened. Once a thread holds a
`CLOSED` post, from this install or fetched from a peer, VISTA MUST refuse to post to it or
continue it, and readers SHALL ignore any post committed after the first `CLOSED` post (it stays
in git history). The orchestrator SHALL treat closure as an expected control-flow signal.

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

#### Scenario: Close is offered only to the opener

- **GIVEN** a thread Alice's install opened
- **WHEN** Bob views it on his install
- **THEN** the lab SHALL NOT offer him Close
- **AND** a request from Bob's install to close it SHALL be refused

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

### Requirement: Per-role attribution on each post

Every debate post SHALL name the role identity that wrote it (for example
`vista-proposer-1a2b3c4d`, or `human` for the operator) and the random host id
of the install that wrote it. Each install SHALL keep one roster per thread: an install's
first run in a thread mints its roster, and its later runs in that thread MUST reuse the same
role identities rather than minting new ones. Rosters of different installs in one thread are
distinct. Posts VISTA writes SHALL also record the VISTA account that wrote them locally, as
today.

#### Scenario: Each role posts under its own identity

- **WHEN** the Proposer and Reviewer each post to a debate thread
- **THEN** the two posts SHALL carry different identities and the same host id

#### Scenario: Continuing a debate keeps its roster

- **GIVEN** a debate that has concluded and is continued for 3 more rounds
- **WHEN** the Proposer posts in the continued rounds
- **THEN** its posts SHALL carry the same identity as its earlier posts

#### Scenario: A teammate's run has its own roster

- **GIVEN** a thread argued by Alice's roster
- **WHEN** Bob continues it from his install
- **THEN** his roles' posts SHALL carry identities of his own and his install's host id
- **AND** to Alice's install they SHALL be `peer-claimed`

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

The lab SHALL require a working system `git`, version 2.34 or later; VISTA does
not ship its own. VISTA SHALL check for it at runtime without triggering an
operating-system install prompt: on macOS, a
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
- **THEN** it SHALL say that git is not installed and that the lab needs it
- **AND** other VISTA features SHALL be unaffected

#### Scenario: Git too old

- **GIVEN** a host whose git is older than 2.34
- **WHEN** a user opens the Hypothesis Lab
- **THEN** it SHALL name the installed version and the version required

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

### Requirement: Viewing a debate survives a concurrent projection

Copying a thread into the database SHALL treat a post another writer stored
first as already present, not as an error. A refresh that fails while a debate
is being read SHALL be logged and SHALL NOT fail the read.

#### Scenario: Two writers project the same post

- **GIVEN** the debate task and a viewer's refresh both copying a new debate's thread
- **WHEN** one stores a post between the other's read and its insert
- **THEN** both SHALL succeed and the post SHALL be stored once

#### Scenario: A refresh fails

- **WHEN** the forum read behind a debate's refresh fails
- **THEN** reading the debate SHALL still return it

### Requirement: The forum remote is verified when a project is saved

Saving a project with a forum repository SHALL initialise that project's working
repository and reach the remote before the save succeeds; an unreachable or
unauthorised remote SHALL fail the save with git's error. Pushes and fetches
to a forum hosted on the configured collaboration server SHALL use the researcher's
collaboration sign-in. Pushes and fetches to another `https://` remote SHALL use the user's
Settings token for the remote's host (github.com or gitlab.com), and the user's own git
credentials otherwise; git MUST NOT prompt interactively. A project SHALL NOT hold a token of
its own. Pointing a project at a repository that already holds threads SHALL join them.

#### Scenario: A typo in the repository URL

- **WHEN** a user saves a project with a forum URL that does not resolve
- **THEN** the save SHALL fail and show git's error in the dialog

#### Scenario: Joining an existing forum

- **GIVEN** a forum repository that already holds threads from another install
- **WHEN** a user saves a project pointing at it
- **THEN** those threads SHALL be listed for the project

#### Scenario: A token that cannot push

- **GIVEN** a private `https://github.com/` forum repository, and a GitHub token in Settings the forge refuses
- **WHEN** a user saves the project
- **THEN** the save SHALL fail and show git's error in the dialog

#### Scenario: The Settings token serves the forum

- **GIVEN** a user with a GitHub token in Settings
- **WHEN** they save the project with a private `https://github.com/` forum repository the token can write to
- **THEN** the save SHALL succeed and debates SHALL publish to it

#### Scenario: Another host uses the system git

- **GIVEN** a forum on an `https://` host other than github.com, gitlab.com or the configured collaboration server
- **WHEN** git fetches or pushes that forum
- **THEN** git SHALL send no token, and the system git's credentials SHALL apply

#### Scenario: A hosted forum uses the collaboration sign-in

- **GIVEN** a researcher signed in to the configured collaboration server
- **WHEN** git fetches or pushes a forum hosted on it
- **THEN** git SHALL send the researcher's collaboration sign-in and no forge token

### Requirement: A failed publish names the Settings token or the system git

When posts cannot be published to a project's forum, the Hypothesis Lab SHALL
say why, from the most recent failed attempt. It SHALL say one of four things:
- the remote refused the credential, naming which one was sent (the collaboration sign-in for
  a hosted forum, the Settings token for the host, or the system git's credentials) and where
  to change it;
- the collaboration server refused the push under one of its rules, naming the rule;
- the remote could not be reached;
- otherwise, git's message.

A refused credential MUST NOT be reported as the forum being unreachable. The reason SHALL
clear once a publish succeeds. The token itself MUST NOT appear in the message.

#### Scenario: The Settings token is refused

- **GIVEN** a GitHub Settings token that cannot push to the project's forum
- **WHEN** a debate's post fails to publish
- **THEN** the lab SHALL say the GitHub token from Settings was refused and point to Settings

#### Scenario: No token was sent

- **GIVEN** a forum reached with the system git's credentials
- **WHEN** a post fails to publish because the remote refused them
- **THEN** the lab SHALL say the system git's credentials were refused, and that a token can be added in Settings for github.com or gitlab.com, or an SSH key or credential helper set up for the system git

#### Scenario: The forum is unreachable

- **WHEN** a post fails to publish because the remote could not be reached
- **THEN** the lab SHALL say the forum could not be reached, and SHALL NOT name a token as the cause

#### Scenario: A later success clears it

- **GIVEN** a failure reason on show
- **WHEN** the next publish succeeds
- **THEN** the reason SHALL no longer be shown

#### Scenario: An old record naming the project's token

- **GIVEN** a stored failure that names the project's token as the credential sent
- **WHEN** the lab page loads
- **THEN** no failure reason SHALL be shown

#### Scenario: The collaboration sign-in has expired

- **GIVEN** a hosted forum and a collaboration sign-in token the server no longer accepts
- **WHEN** a post fails to publish
- **THEN** the lab SHALL say the collaboration sign-in was refused and point to the server's sign-in page for a new token
- **AND** the post SHALL be published by the first sync after a new token is saved

### Requirement: Git receives the forum's token only on the way to its remote

Git SHALL receive the forum's credential (the Settings token, or the collaboration sign-in for
a hosted forum) only on commands that reach the remote, only for an `https://` remote or a
collaboration server on loopback, and scoped to that remote's URL. It MUST NOT appear in a
command's arguments or in the repository's configuration. The project dialog SHALL say which
credential the forum will use: the collaboration sign-in for a hosted forum, the Settings token
for its host when one is saved, else the system git's credentials.

#### Scenario: The token goes only to the forum repository

- **GIVEN** a GitHub token in Settings and an `https://github.com/` forum URL
- **WHEN** git fetches or pushes the forum
- **THEN** git SHALL send the token to that URL and to no other
- **AND** a forum URL over plain `http://` or SSH SHALL receive no token

#### Scenario: The dialog says which credential applies

- **GIVEN** a user with a GitHub token in Settings
- **WHEN** they enter an `https://github.com/` forum URL in the project dialog
- **THEN** the dialog SHALL say the Settings token will be used

#### Scenario: The dialog names the system git elsewhere

- **WHEN** a user enters an `https://` forum URL on a host other than github.com, gitlab.com or the collaboration server, or an SSH URL on any host
- **THEN** the dialog SHALL say the system git's credentials will be used, such as an SSH key or a credential helper
- **AND** it SHALL offer no token field

#### Scenario: Settings not loaded yet

- **WHEN** the project dialog has not yet learned which tokens the user has saved in Settings
- **THEN** it SHALL name no credential, rather than one that may be wrong

#### Scenario: The sign-in goes only to the collaboration server

- **GIVEN** a hosted forum and a GitHub token in Settings
- **WHEN** git fetches or pushes the hosted forum
- **THEN** git SHALL send the collaboration sign-in to the server's forum URL only
- **AND** it SHALL NOT send the GitHub token

### Requirement: Each debate role has its own model

Opening a debate SHALL let the researcher choose a model for each role (Proposer, Reviewer
and Referee) or leave a role on the default. A role left on the default SHALL use the
researcher's default model as it is when each run of the debate starts. Continuing a debate
SHALL offer the same choice, starting from the models this install's roster used last in that
thread, or from the default for every role when this install has not run in it, and SHALL keep
them unless the researcher changes them there. A role's model MUST NOT change while a run
is in progress. Each role's model SHALL be a model name sent to the provider of the researcher
running it. The debate view SHALL show each of this install's roles' model, and each post a
role writes SHALL record locally the model that wrote it.

#### Scenario: A stronger Referee

- **GIVEN** a researcher whose default is model A
- **WHEN** they open a debate with the Referee set to model B and the other roles left on
  the default
- **THEN** the Proposer and Reviewer argue with A, and the verdict is written by B

#### Scenario: Continue keeps the debate's models

- **GIVEN** a concluded debate whose Referee used model B
- **WHEN** the researcher continues it for 2 more rounds without changing the models
- **THEN** the Referee's posts in the new rounds are written by B

#### Scenario: Continue may change a role's model

- **GIVEN** a concluded debate whose Reviewer used model A
- **WHEN** the researcher continues it with the Reviewer set to model C
- **THEN** the Reviewer's posts in the new rounds are written by C, and its earlier posts
  still record A

#### Scenario: Changing the default between runs

- **GIVEN** a concluded debate whose Proposer was left on the default, which was A
- **WHEN** the researcher changes the default to B and continues the debate
- **THEN** the Continue form shows the Proposer on "Default (B)", and its new posts are
  written by B

#### Scenario: Each post shows its model

- **WHEN** a researcher views a debate whose roles used different models
- **THEN** the view names each role's model, and each role's post names the model that
  wrote it

#### Scenario: A teammate's first Continue starts from their defaults

- **GIVEN** a thread argued only by Alice's install, whose Referee used model B
- **WHEN** Bob opens the Continue form on his install
- **THEN** every role SHALL start on Bob's default

### Requirement: Every install lists every thread on the forum

A project's Hypothesis Lab SHALL list every thread on the project's forum, whichever install
opened it, and SHALL show for each thread who opened it, its status and its current verdict.
VISTA SHALL fetch the forum's threads when the lab is opened, and then at most about every
30 seconds while it stays open. It MUST NOT fetch the forum's threads while no lab is open. A
fetch that fails SHALL leave the list showing the threads it last read, and the failure SHALL
be reported as a failed publish is.

#### Scenario: A teammate's thread appears

- **GIVEN** Alice and Bob whose projects point at the same forum repository
- **AND** Alice opens a debate
- **WHEN** Bob opens his project's Hypothesis Lab
- **THEN** Alice's thread SHALL be listed, marked as opened by another install

#### Scenario: A thread opened while the lab is open

- **GIVEN** Bob's Hypothesis Lab open on a project
- **WHEN** Alice opens a debate on the same forum
- **THEN** her thread SHALL appear in Bob's list within about a minute, without a reload

#### Scenario: Nothing syncs while the lab is closed

- **GIVEN** no Hypothesis Lab page open on an install
- **WHEN** time passes
- **THEN** that install SHALL NOT fetch any project's forum to list its threads

#### Scenario: The forum cannot be reached

- **GIVEN** a lab that has listed a forum's threads
- **WHEN** a later fetch of the forum fails
- **THEN** the lab SHALL still list the threads it read before
- **AND** it SHALL say the forum could not be reached

### Requirement: Anyone may post to a thread that is not closed

A researcher SHALL be able to post to any thread listed in their lab that holds no `CLOSED`
post, whether or not their install opened it and whether or not a run is live in it. A
human's post MUST NOT start an agent turn. The agents of whichever run is live SHALL read it at
their next turn. A simulation result that arrives after its turn's wait SHALL be posted the same
way, whether or not its run is still live.

#### Scenario: Bob comments on Alice's concluded thread

- **GIVEN** a concluded thread Alice's install opened
- **WHEN** Bob posts new evidence to it from his install
- **THEN** the post SHALL be committed and published to the thread
- **AND** no agent turn SHALL start because of it

#### Scenario: A post during someone else's run

- **GIVEN** a thread in which Alice's run is live
- **WHEN** Bob posts to it
- **THEN** his post SHALL be accepted
- **AND** Alice's agents SHALL see it in the thread at their next turn

#### Scenario: A late simulation result

- **GIVEN** a simulation commissioned in Alice's run, whose result arrives after that run ended and while Bob's run is live
- **WHEN** the result is posted
- **THEN** it SHALL be posted to the thread and Bob's agents SHALL read it at their next turn

### Requirement: Anyone may continue a thread with their own agents

A researcher SHALL be able to Continue any listed thread that holds no `CLOSED` post and has
no live run, for 1 to 10 rounds. The run SHALL execute on the researcher's own install, under
the researcher's own models, project skills, knowledge bases and HPC credentials, and its
agents SHALL read the whole thread. It SHALL end with its own Referee's verdict. The
researcher who opened the thread SHALL be able to Continue it on the same terms.

#### Scenario: Bob reopens a settled question

- **GIVEN** a thread Alice's install opened and concluded, holding Bob's new evidence
- **WHEN** Bob continues it for 3 rounds
- **THEN** his install's Proposer, Reviewer and Referee SHALL argue 3 rounds in that thread
- **AND** his Referee SHALL post a verdict that rules on the whole thread

#### Scenario: Bob's run uses Bob's resources

- **GIVEN** a project whose skills offer simulations, where Bob has credentials for a cluster Alice does not
- **WHEN** Bob's Reviewer commissions a simulation in his run
- **THEN** it SHALL be submitted with Bob's credentials, on a cluster available to Bob

### Requirement: One live run per thread

A thread SHALL have at most one live run. A run SHALL begin with a `RUN` post naming its roster
and its round budget, and SHALL be live from that post until its Referee posts `DONE` or
`BLOCKED`, the thread is closed, or no post by its roster has been made for the run expiry.
The run expiry SHALL be the longest a turn may wait for a simulation plus 15 minutes. A
Continue SHALL be refused while another run is live in the thread, with a message naming whose
run it is. Two installs that each start a run before either has seen the other's `RUN` post
MAY both run; VISTA SHALL show both runs.

#### Scenario: Continue while a run is live

- **GIVEN** a thread in which Alice's run is live
- **WHEN** Bob asks to continue it
- **THEN** the Continue SHALL be refused, saying Alice is continuing the debate
- **AND** Bob SHALL still be able to post

#### Scenario: A run waiting on a simulation is not expired

- **GIVEN** a run whose Reviewer has waited 30 minutes for a simulation without posting, with a 30-minute job-wait limit
- **WHEN** another researcher asks to continue the thread
- **THEN** the Continue SHALL be refused because the run is still live

#### Scenario: An abandoned run expires

- **GIVEN** a run whose install stopped mid-run, and no post from its roster for longer than the run expiry
- **WHEN** another researcher asks to continue the thread
- **THEN** the Continue SHALL be accepted

#### Scenario: A failed run ends

- **GIVEN** a run whose Referee cannot rule
- **WHEN** the run fails
- **THEN** a `BLOCKED` post from its Referee SHALL end the run, and others SHALL be able to Continue

### Requirement: The current verdict travels in the thread

A Referee's `DONE` post SHALL carry the structured verdict (claim, mechanism, falsifiable
predictions, confidence, open risks) in its post file, beside its Markdown body, so that every
install reads the same verdict from git. A thread's current verdict SHALL be the one in its
latest `DONE` post, whichever install wrote it. A `DONE` post without a structured verdict, from
before this change or from a peer, SHALL show its body as the verdict.

#### Scenario: Bob's verdict replaces Alice's

- **GIVEN** a thread concluded by Alice's run, then continued and concluded by Bob's
- **WHEN** either of them views the thread
- **THEN** the current verdict SHALL be Bob's Referee's
- **AND** Alice's verdict SHALL remain readable in the thread

#### Scenario: A verdict from an older install

- **GIVEN** a `DONE` post whose file has no structured verdict
- **WHEN** the thread is viewed
- **THEN** its body SHALL be shown as the verdict

### Requirement: Another install's run is shown from the thread

While another install's run is live in a thread, the lab SHALL show whose run it is, its round
budget, how many posts its roster has made and how long ago the last one was, worked out from
the thread alone. VISTA MUST NOT publish its own runs' live activity (such as which job a role
is waiting for) to the forum.

#### Scenario: Bob watches Alice's run

- **GIVEN** Alice's run of 3 rounds in which her roster has made 2 posts, the last 4 minutes ago
- **WHEN** Bob views the thread
- **THEN** the lab SHALL show that Alice's install is running 3 rounds, with 2 posts so far, the last 4 minutes ago

### Requirement: Existing debates carry over

Debates created before this change SHALL appear in the lab as threads with one run each, with
their posts, verdicts, rosters and models, and with continuing them unchanged. A debate whose
thread is missing from the forum SHALL keep its stored posts.

#### Scenario: A debate from before the change

- **GIVEN** a debate concluded before this change
- **WHEN** its install opens the Hypothesis Lab after the upgrade
- **THEN** the debate SHALL be listed with its posts and verdict
- **AND** it SHALL be possible to continue it with the same roster
