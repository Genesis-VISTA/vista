## ADDED Requirements

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

## MODIFIED Requirements

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
