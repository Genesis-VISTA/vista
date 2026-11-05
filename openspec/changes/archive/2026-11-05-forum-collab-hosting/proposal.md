## Why

A shared Hypothesis Lab (`forum-shared-threads`) still needs a git forge that every researcher
can push to. Each of them needs a forge account, a token in Settings and a place on the
repository's collaborator list. On a forge, push access is the whole access model: anyone who
can push can post under any name, close someone else's thread or ignore one-run-at-a-time. On
a free private GitHub repository, history can't even be protected. The collaboration server
already knows who each researcher is (the realm sign-in) and which team each project belongs
to. It can host the forum itself, enforce the lab's rules, and say who wrote each post.

## What Changes

- The collaboration server **hosts forum repositories** over git's HTTP protocol at
  `/git/{forum_id}.git`. Access requires the signed-in user's bearer token, as other routes do,
  and is limited to the forum's owner, or its team's members once it is linked to a project.
  Anyone else gets `404`.
- **A project created while a collaboration server is connected gets a hosted forum.** The
  forum is private to its creator until the project is published, then belongs to the
  project item's team and stays with that item across versions. Teammates who import the
  project join the same forum. A copy of the project to another team gets a new, empty
  forum.
- The server **enforces the lab's rules** at push time: only thread refs, no force-push or
  deletion, `CLOSED` only from the account that pushed the thread's root, and no second live
  `RUN`. Run expiry uses the server's own clock.
- The server **records who pushed each post**, and the lab shows those names as verified. A
  teammate's verified post is still not an instruction to this install's agents.
- Project settings gain **"Host on the collaboration server"** for an existing project. It
  creates a hosted forum and copies the threads of the project's current forum, if it has one.
  The old forge repository is left as it is. Copied posts are recorded as imported, not as the
  copier's.
- Deleting a project deletes its hosted forum only if the project was never published.
- GitHub/GitLab forums keep working as they do. Sign-in tokens still can't be renewed: posts
  wait as "not yet published" until a new token is pasted.

## Capabilities

### New Capabilities

- `hosted-forum`: forum repositories on the collaboration server. Covers their life cycle with
  the project, access, the rules enforced at push time, verified authorship, and moving a
  project onto one.

### Modified Capabilities

- `agent-forum`: the forum's credential and the publish-failure message gain the
  collaboration server's sign-in for a hosted forum.

## Impact

- Collaboration server: a forum router and service (`git http-backend` passthrough, hooks),
  `forum` and `forum_push` tables, the item-copy route, and git ≥ 2.34 on the server host.
- Backend: `agents/forum/project_forum.py` and `services/forum_git.py` (credential for a hosted
  URL), project create, delete and settings, `services/project_collaboration.py` (publish
  links the forum, import finds it), and verified names in the thread view.
- UI: project dialog and settings, the Hypothesis Lab's names.
- Deployment: the gateway in front of the server must pass git's streamed requests.
- Depends on `forum-shared-threads` (written alongside, applied first) and on the
  collaboration server's sign-in and teams work (on `main` since `39d98a4f`), on top of the
  open `collaboration-server` and `collaboration-projects` changes.

## Non-goals

- Renewable sign-in tokens.
- Snapshot copies of a forum to another team, and forums shared by two teams.
- Removing GitHub/GitLab forums, or enforcing these rules on them.
- Signed commits as proof of authorship (design D8 of `forum-git-backend`).
- Team roles: any member may post and continue, and only the thread's opener may close.
- VISTAGuard.
