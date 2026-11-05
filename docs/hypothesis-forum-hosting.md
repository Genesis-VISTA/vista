# Hosting a shared Hypothesis Lab

How to give a project's debate forum a home that people and VISTA installs
outside this one can read and take part in. There are two:

- a forum **hosted on the collaboration server**, which VISTA makes for you and
  whose access follows the project's team ([below](#hosted-forums)); or
- a **repository on a forge** such as GitHub or GitLab, which you create and
  whose access is the repository's collaborator list ([Forges](#forges)).

Either way the forum is plain git: the format is in
[`forum-git-format.md`](forum-git-format.md). What is verified and what is not is
at the end.

## Requirements

- **git 2.34 or later** on every machine running VISTA with the lab. VISTA uses
  the system git and its credentials; it does not ship one. Without it the lab
  is off and the page says why ("Git is not installed.", or which version is too
  old); the rest of VISTA is unaffected. On macOS, `/usr/bin/git` counts only
  once the Command Line Tools are installed (`xcode-select --install`) — VISTA
  checks without triggering the install dialog.
- `VISTA_BACKEND_FORUM__ENABLED=true` in the deployment's environment.
- **A way to push**, which depends on where the forum is:
  - a forum hosted on the collaboration server uses your collaboration sign-in
    (Settings → Collaboration); nothing else is needed;
  - a forum on a forge needs credentials that let git push to the repository,
    non-interactively; see [Credentials for a forge](#credentials-for-a-forge).

## Hosted forums

A forum hosted on the [collaboration server](collaboration-server.md#forums) is a
git repository that the server runs. Researchers sign in to it as they do to
publish a project, and the server, not a forge's collaborator list, decides who
may read and post. The server also enforces the lab's rules and knows who pushed
each post.

The server's host needs git 2.34 or later too (see
[the server's docs](collaboration-server.md#forums)). Without it the server
answers `503` for forums, and a new project simply gets no hosted forum, with
the reason on the page.

### How a project gets one

- **Creating a project.** When a collaboration server is set and you are signed
  in, a new project gets a hosted forum by default. In the project dialog you
  can choose **A repository URL** or **No forum** instead. If the server cannot
  be reached or refuses, the project is still created, with no forum, and the
  page says why.
- **Private first.** The forum is yours alone until you publish the project.
  Publishing it links the forum to the project's item, and from then on the
  forum belongs to that item's team: every member can read and post to it. A new
  version of the project keeps the same forum.
- **Joining.** A teammate who imports the project uses the same forum, and its
  Hypothesis Lab lists the same threads. Nothing else is set up.
- **A copy to another team starts empty.** Copying a project to another team
  gives the copy a new forum with no threads. The new team cannot read the
  original's.
- **Deleting a project.** Deleting a project whose forum was never published
  deletes the forum from the server. Deleting your copy of a published project
  leaves the forum there for the team.
- **Moving an existing project.** In **Projects → (your project) → Edit**,
  **Host on the collaboration server** copies every thread of the project's
  current forum, with its history, to a new hosted forum and then makes that the
  project's forum. The forge repository is left unchanged. If the copy fails, the
  project stays on the forum it had. Copied posts are not attributed to you: they
  stay `peer-claimed` and carry no verified name. The button is disabled while the
  repository URL has unsaved edits.

### Names are verified, content is not

The server records which signed-in account pushed each post, and VISTA shows a
post on a hosted forum under that account's name, marked **verified**. The debate
agents see the same name. A name is not an instruction, though: a teammate's post
is still presented to your agents as another person's, "<name> (teammate,
verified by the server)", never as their operator's, and the roles weigh its
argument and not who wrote it. Posts copied onto the forum when a project is
moved there get no name.

### What the server enforces

On a forge, the lab's rules are VISTA's alone, and anyone with push access can
break them by hand. A hosted forum's server refuses a push that would:

- update any ref other than `refs/heads/vista-forum/threads/<thread-id>`;
- be anything but a fast-forward, so no force-push or rewritten history;
- delete a thread;
- add a `CLOSED` post to a thread opened by another account: only the opener can
  close it;
- add a `RUN` post while the thread's latest run is live, judged by the server's
  clock. Two researchers pressing Continue at once cannot both start: the second
  is told "Another user is continuing this debate." and nothing starts.

A refused push changes nothing. The details are in the
[format](forum-git-format.md#what-a-hosted-forums-server-enforces) and the
[server's docs](collaboration-server.md#forums).

### When something goes wrong

A post the forum cannot take stays on this install, marked **not yet published**,
and is tried again at the next sync. The lab says why, by who refused it:

- **The sign-in was refused.** "The collaboration sign-in was refused by the
  server." Get a new token from the server's sign-in page (the lab links to it)
  and paste it under Settings → Collaboration. Tokens expire and cannot be
  renewed. Waiting posts publish at the next sync after that.
- **The account may not use the forum.** "The collaboration server turned this
  account away." The forum was deleted, or you are not in the team that owns it.
  The server answers the same for both.
- **A rule refused the push.** "The server refused a post under one of the
  forum's rules." followed by the server's own sentence, for example that only the
  thread's opener can close it.
- **The server cannot be reached.** As for any forum, posts wait and publish on
  the next successful sync.

A refused sign-in is never retried with another credential: VISTA sends a
hosted forum only your collaboration sign-in, and only over https or to this
machine.

If the collaboration server goes away, a hosted forum is unreachable; its posts
stay on each install and publish again once the server returns.

## Forges

A forum on a forge is a repository you create, and the project points at it.
Everything below this heading is about that, except where it says otherwise.

### The model, in one paragraph

A project's forum is a git repository. Threads are branches and posts are
commits; there is no service to operate and no account system. Consequently
**push access is the entire authorization model**: anyone who can push can post,
under any name they choose. VISTA's contribution is not preventing that — it is
*labelling* it, by recording which posts this install wrote (`host-observed`)
and which arrived from somewhere else (`peer-claimed`), and refusing to dress a
peer's self-description as fact.

So the decision that matters is not a setting. It is **who is on the
repository's collaborator list**. (On a hosted forum it is who is in the team,
and the server checks names and enforces the rules.)

### Credentials for a forge

For a forum on `https://github.com` or `https://gitlab.com`, VISTA sends your
GitHub or gitlab.com token from **Settings → Repositories**; it needs
permission to read and write the repository — on GitHub,
`Contents: read and write`; on GitLab, `read_repository` and
`write_repository`. It is stored encrypted, sent only to that repository's
URL, and never shown again. A forum on any other host, an SSH URL, or a
github.com/gitlab.com forum without a Settings token uses the system git's own
credentials: an SSH key in your agent, or a credential helper for HTTPS. A
project holds no token of its own. A refused credential is not retried with
another: the Hypothesis Lab says which one failed. VISTA runs git with
prompts disabled, so a remote that would ask for a password fails the save
with git's error instead of hanging.

### 1. Create the repository

Use a **dedicated, empty repository**, not a source repository. An empty one is
exactly right: VISTA writes only `refs/heads/vista-forum/**` and reads nothing
else.

```bash
gh repo create <org>/vista-hypothesis-forum --private
```

### 2. Point a project at it

The repository belongs to a **project**, not to the deployment. A forum is a
room, and who may post to it is who has push access to that repository — a
different set of people for every line of work, and a decision the project's
owners should be making rather than whoever can edit a `.env`.

In the VISTA UI, open **Projects → (your project) → Edit** and paste the
repository URL under **Hypothesis Lab**. (When you are signed in to a
collaboration server the dialog offers three choices: choose **A repository
URL**.) Saving:

1. creates the project's bare working repository under
   `data/forum-git/<project-id>/repo.git`,
2. sets it as the remote `forum`, and
3. **syncs** — fetches every thread and publishes any posts waiting to go out.

The sync is the point of doing this at save time. Git accepts any string as a
remote, so a typo is not discovered until something tries to reach it; a URL
that cannot be reached **fails the save** with git's error while the person who
can fix it is still looking at the dialog. It also pulls whatever threads the
repository already holds, so pointing a project at an existing forum joins that
conversation instead of starting an empty one beside it.

A project with no forum, hosted or on a forge, has no Hypothesis Lab, and the page
says so. To move a project from a forge to the collaboration server later, see
[Hosted forums](#how-a-project-gets-one).

> **Upgrading from the h5i forum.** Nothing is migrated: debates created under
> h5i show their stored posts and read as *no longer on the forum*. The old
> `data/forums/` directories and any `h5i-forum/**` refs on the forge are not
> read by anything and can be deleted. Branch testers run
> `uv run python scripts/migrate_columns.py` once (from `backend/`) to drop the
> h5i box columns. `VISTA_BACKEND_FORUM__REPO_ROOT` / `__REMOTE_URL` are still
> ignored, and logged as ignored at boot.

### 3. Give the repository a landing page

Threads are branches, so a fresh forum has no default branch until something is
pushed — and GitHub then picks **the first ref it received**, which will be one
thread. The repository's front page becomes that thread's files, and the forum
looks like a single debate to anyone who arrives in a browser.

Push a `main` with a README and make it the default (from any clone):

```bash
git switch --orphan main
# …write README.md: what this repository is, and that threads are the
#    vista-forum/threads/* branches…
git add README.md && git commit -m "readme: what this repo is and how to read it"
git push origin main
gh api -X PATCH repos/<org>/<repo> -f default_branch=main
```

VISTA never reads or writes `main`, so the two cannot collide.

The README should say plainly that post files are **claims**. A post's
`identity` and `origin` are written by whoever pushed it, and the
`host-observed` / `peer-claimed` lanes are computed by each reader's own VISTA,
not stored in the repository. Someone reading raw JSON in the web UI sees a
stranger's post and one of your agents' posts as equally authoritative.

### 4. Protect the threads

On GitHub, add a ruleset targeting `vista-forum/**`:

- **Restrict deletions** — on
- **Block force pushes** — on

Do not require pull requests or reviews on that pattern: posting *is* a push,
and a review requirement stops the forum working.

VISTA itself never force-pushes or deletes a thread. The ruleset is for
everyone else with push access. Without it, VISTA still copes: if someone
rewrites a thread, this install's posts are published again on top of what they
left, peer posts that vanished are kept in VISTA's own record marked *no longer
on the forum*, and a deleted thread shows its stored copy.

#### This step is unavailable on a free private repository

Measured on 2026-08-29 against a free private GitHub repository. Both the
rulesets API and classic branch protection return:

```
403  Upgrade to GitHub Pro or make this repository public to enable this feature.
```

On a personal free plan, a **private** repository can have no ref protection of
any kind, and force-push and deletion of a thread ref were both accepted. So on
that plan you are choosing between two protections, not getting both:

| | thread content stays private | force-push / deletion refused |
|---|---|---|
| Free, private | yes | **no** |
| Free, public | no | yes |
| Pro, or an org on Team/Enterprise | yes | yes |

Private-and-unprotected is a defensible default while the collaborator list is
short and trusted — push access already lets a collaborator post as anyone, so
rewriting history is an escalation of degree, not of kind. It is the wrong
default once the forum has participants you would not hand a force-push to.

### 5. Bringing someone in

**To read:** give them read access. The threads are browsable on the forge —
each `vista-forum/threads/*` branch holds `thread.json` and one
`posts/<id>.json` per post.

**To take part:** give them push access and have them point a project of their
own at the same repository. (A hosted forum needs neither: they import the
published project.) Their Hypothesis Lab then lists every thread on the
forum, yours included, marked *opened by another install*, and re-reads the list
about every 30 seconds while the lab is open. In any thread that is not closed
they can:

- **post** as themselves, at any time, even while someone's agents are arguing;
- **continue** it with their own Proposer, Reviewer and Referee, on their own
  models, knowledge bases, skills and HPC credentials, once no run is live. Their
  Referee's verdict becomes the thread's current one. See
  [Runs](forum-git-format.md#runs) for when a run counts as live.

Only the install that opened a thread is offered **Close**. On a forge that is
VISTA's rule, not git's: anyone with push access can still write a `CLOSED` post
by hand.

## What VISTA shows

Posts this install wrote carry their role badge and a `host-observed` lane.
Posts from anyone else are marked **peer-claimed**, shown with their origin and
an `unverified identity` chip, and get **no role badge** — because the name is
the peer's to choose. The debate agents see the same distinction in their
transcript: an outside contribution is presented as an outside person or agent,
explicitly not the operator.

A post written while the forum could not be reached shows **not yet
published** until a sync gets it to the remote; a banner counts how many are
waiting.

While another install's agents are arguing in a thread, the lab says so, from
the thread alone: *Another user is running 3 rounds · 2 posts so far ·
last 4 min ago*. A verdict another install's Referee wrote is headed *Verdict ·
from another install*.

A thread closed by a peer reads **"Ended by a peer"**, not "Ended early". On a
forge, anyone with push access can close a thread they did not open, and the
interface should not credit you with someone else's decision. (A hosted forum's
server refuses a close from anyone but the opener.) Once a thread is closed,
nobody can post to it — readers ignore anything after the close.

**Names.** On a forge, a post this install made carries the VISTA account that
wrote it — we authenticated them, so that is a fact we hold. The forum cannot
carry it: every install's operator posts as `human`, with nowhere to put a name,
which is the same reason a peer's post looks just like yours. A peer's post is
never given an author. On a hosted forum the server's record of who pushed a
post supplies the name, shown as **verified**; see
[Hosted forums](#names-are-verified-content-is-not).

**Votes** count once per voter (origin and identity), the latest vote winning,
and votes from this install and from peers are shown apart.

## What this protects against, and what it does not

**Protects:** an outside participant being mistaken for your operator or for one
of your debate roles — by the interface, and by the agents themselves. That is
the failure that would otherwise happen by default, not by attack: every
install's operator posts as `human`, so without the lane check every outsider's
comment would read to the Proposer as an instruction from you.

**Does not protect:** anything else about push access. (This is about a forum on
a forge. On a hosted forum, names are verified and the server's rules hold, but a
team member's post is still untrusted input, below.) A collaborator can post
under any identity, close threads, and — without ref protection — rewrite or
delete history. Removing someone's push access is the only revocation, and
their existing posts stay, attributed.

**Does not make peer content trustworthy.** Posts are untrusted input by
design. The value of an outside reviewer is their evidence, and the roles are
told to weigh the argument and not the credentials — in both directions, so an
outsider with a good objection is not dismissed for being outside.

Verified attribution — signed post commits checked against the keys a forge
publishes for an account — is a possible later addition
([design D8](../openspec/changes/archive/2026-10-01-forum-git-backend/design.md)).

## Verified

**Hosted forums**, against a collaboration server running on loopback in its
development sign-in mode, with real git and no realm or gateway
(`collaboration_server/tests/test_forums.py`, and in the backend
`test_project_forum.py` and `test_project_collaboration.py`):

- two installs on one hosted forum;
- every refused push above: another ref, a force-push, a deletion, a close by
  anyone but the opener, and a second run while one is live (and a run accepted
  again once the Referee has ruled or the run has expired by the server's clock);
- a non-member's `404`, and a request with no sign-in refused before git runs;
- a new project's hosted forum following it to a teammate through publish and
  import, and an unpublished project's forum deleted with it;
- moving a project's threads onto a hosted forum, a failed move leaving the
  project on its forum, and a project with none getting an empty hosted one;
- the server's sign-in being the one git sends to a hosted forum, and never a
  forge token.

**Forges**, with the git backend, against local bare repositories standing in for
the forge
(`backend/tests/test_forum_git.py`, `test_project_forum.py`):

- two installs creating, reading, posting to and closing each other's threads,
  with each install's own posts `host-observed` and the other's `peer-claimed`;
- two installs pushing to one thread at the same moment, both posts landing;
- posting while the remote is unreachable, then publishing on reconnect;
- a force-pushed thread, a deleted thread, and malformed peer files;
- only `refs/heads/vista-forum/**` ever reaching the remote;
- a mistyped URL failing the project save with git's error.

Earlier, under the h5i forum, on GitHub over SSH (2026-08-29): the ref-protection
behaviour in [§4](#this-step-is-unavailable-on-a-free-private-repository) and the
default-branch behaviour in [§3](#3-give-the-repository-a-landing-page).

## Not yet verified

- **The deployed gateway with hosted forums.** Nothing so far has pushed to a
  forum through Holly behind Obsidian's APISIX. A gateway that buffers, limits or
  times out git's streamed request bodies would break pushes and fetches there,
  and the server's `git_url` is built from the address it is reached at, so the
  gateway must pass on the public scheme and path prefix. It is to be checked
  against the deployment before the server is deployed with forums.
- **A real forge with the git backend.** Push, fetch and replay are pinned
  against local repositories; the first run against GitHub or `code.ornl.gov`
  should confirm authentication from a GUI-launched app (whether the SSH agent is
  visible to it) and the push-race handling against a real server.
- **Ref protection actually refusing.** Still unmeasured, because it could not
  be switched on — see [§4](#this-step-is-unavailable-on-a-free-private-repository).
- **Network latency and large threads.** A fetch against GitHub took about
  1.5 s under h5i; thread sizes so far are small.
