# Hosting a shared Hypothesis Lab

How to publish a project's debate forum so people and VISTA installs outside
this one can read it and take part.

The forum is plain git: the format is in [`forum-git-format.md`](forum-git-format.md).
What is verified and what is not is at the end — the git backend has been
exercised against local bare repositories, and the forge-specific facts below
were measured on GitHub under the earlier h5i forum and still apply because
they are about refs, not about h5i.

## The model, in one paragraph

A project's forum is a git repository. Threads are branches and posts are
commits; there is no service to operate and no account system. Consequently
**push access is the entire authorization model**: anyone who can push can post,
under any name they choose. VISTA's contribution is not preventing that — it is
*labelling* it, by recording which posts this install wrote (`host-observed`)
and which arrived from somewhere else (`peer-claimed`), and refusing to dress a
peer's self-description as fact.

So the decision that matters is not a setting. It is **who is on the
repository's collaborator list**.

## Requirements

- **git 2.34 or later** on every machine running VISTA with the lab. VISTA uses
  the system git and its credentials; it does not ship one. Without it the lab
  is off and the page says why ("Git is not installed.", or which version is too
  old); the rest of VISTA is unaffected. On macOS, `/usr/bin/git` counts only
  once the Command Line Tools are installed (`xcode-select --install`) — VISTA
  checks without triggering the install dialog.
- **Credentials that let that git push** to the forum repository, non-interactively:
  an SSH key in your agent, or a credential helper for HTTPS. VISTA runs git with
  prompts disabled, so a remote that would ask for a password fails the save
  with git's error instead of hanging.
- `VISTA_BACKEND_FORUM__ENABLED=true` in the deployment's environment.

## 1. Create the repository

Use a **dedicated, empty repository**, not a source repository. An empty one is
exactly right: VISTA writes only `refs/heads/vista-forum/**` and reads nothing
else.

```bash
gh repo create <org>/vista-hypothesis-forum --private
```

## 2. Point a project at it

The repository belongs to a **project**, not to the deployment. A forum is a
room, and who may post to it is who has push access to that repository — a
different set of people for every line of work, and a decision the project's
owners should be making rather than whoever can edit a `.env`.

In the VISTA UI, open **Projects → (your project) → Edit** and paste the
repository URL under **Hypothesis Lab**. Saving:

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

A project with no repository has no Hypothesis Lab, and the page says so.

> **Upgrading from the h5i forum.** Nothing is migrated: debates created under
> h5i show their stored posts and read as *no longer on the forum*. The old
> `data/forums/` directories and any `h5i-forum/**` refs on the forge are not
> read by anything and can be deleted. Branch testers run
> `uv run python scripts/migrate_columns.py` once (from `backend/`) to drop the
> h5i box columns. `VISTA_BACKEND_FORUM__REPO_ROOT` / `__REMOTE_URL` are still
> ignored, and logged as ignored at boot.

## 3. Give the repository a landing page

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

## 4. Protect the threads

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

### This step is unavailable on a free private repository

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

## 5. Bringing someone in

**To read:** give them read access. The threads are browsable on the forge —
each `vista-forum/threads/*` branch holds `thread.json` and one
`posts/<id>.json` per post.

**To take part — not in the UI yet.** A second VISTA install pointed at the same
repository fetches your threads (its agents can cite them as precedent), but its
Hypothesis Lab lists only debates it opened itself: there is no way yet to open,
post to or close someone else's thread from the page. The forum client supports
it — the two-install check did exactly that through `services/forum_git.py`, and
every such post reads as `peer-claimed` on your side — so what is missing is the
"join a thread" surface, not the format.

## 6. What VISTA shows

Posts this install wrote carry their role badge and a `host-observed` lane.
Posts from anyone else are marked **peer-claimed**, shown with their origin and
an `unverified identity` chip, and get **no role badge** — because the name is
the peer's to choose. The debate agents see the same distinction in their
transcript: an outside contribution is presented as an outside person or agent,
explicitly not the operator.

A post written while the forum could not be reached shows **not yet
published** until a sync gets it to the remote; a banner counts how many are
waiting.

A thread closed by a peer reads **"Ended by a peer"**, not "Ended early". Anyone
with push access can close a thread they did not open, and the interface should
not credit you with someone else's decision. Once a thread is closed, nobody can
post to it — readers ignore anything after the close.

**Names.** A post this install made carries the VISTA account that wrote it —
we authenticated them, so that is a fact we hold. The forum cannot carry it:
every install's operator posts as `human`, with nowhere to put a name, which is
the same reason a peer's post looks just like yours. A peer's post is never
given an author.

**Votes** count once per voter (origin and identity), the latest vote winning,
and votes from this install and from peers are shown apart.

## What this protects against, and what it does not

**Protects:** an outside participant being mistaken for your operator or for one
of your debate roles — by the interface, and by the agents themselves. That is
the failure that would otherwise happen by default, not by attack: every
install's operator posts as `human`, so without the lane check every outsider's
comment would read to the Proposer as an instruction from you.

**Does not protect:** anything else about push access. A collaborator can post
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

With the git backend, against local bare repositories standing in for the forge
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

- **A real forge with the git backend.** Push, fetch and replay are pinned
  against local repositories; the first run against GitHub or `code.ornl.gov`
  should confirm authentication from a GUI-launched app (whether the SSH agent is
  visible to it) and the push-race handling against a real server.
- **Ref protection actually refusing.** Still unmeasured, because it could not
  be switched on — see [§4](#this-step-is-unavailable-on-a-free-private-repository).
- **Network latency and large threads.** A fetch against GitHub took about
  1.5 s under h5i; thread sizes so far are small.
