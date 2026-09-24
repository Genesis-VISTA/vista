## Why

The Hypothesis Lab (`agent-forum`) is built on `h5i forum`, and h5i removed that
feature in v0.4.0 ([h5i-dev/h5i#586](https://github.com/h5i-dev/h5i/pull/586),
merged 2026-08-31, ~16.5k lines, no replacement or migration path). The branch
only works on a pinned v0.3.8 that will never be fixed; the documented install
now fetches a build with no `forum` command, and CI cannot notice because it
replays a fake h5i. h5i also ships no Windows build, which blocks a Windows
desktop release. Agreed with Junqi Yin (the branch's author) on 2026-09-23 to
replace it with a git backend VISTA owns. On 2026-09-24 Junqi answered the
open questions on the MR: require system git (the usual practice for desktop
tools that drive git), leave signing for later (posts still carry an identity
and host id), and no existing threads are worth migrating.

## What Changes

- **BREAKING (branch-internal):** `services/h5i_forum.py` is replaced by a git
  client exposing the same interface (`create_thread`, `list_threads`,
  `read_thread`, `post_as`, `post_as_human`, `close_thread`, `vote`, `sync`,
  `set_remote`, `remote`, `stage_attachment`). The debate engine, the
  `debate_*` tables, the API and the Hypothesis Lab UI keep their shape.
- The forge is the only server. Each desktop install keeps one working repo per
  project and syncs it with that project's `forum_repo_url`. There is no VISTA
  server.
- Threads are append-only branches under `refs/heads/vista-forum/threads/<id>`;
  each post is one commit adding one JSON file (plus its attachment, capped at
  1 MB). Close and votes are post kinds.
- Posting is local-first: a post commits locally at once and publishes when the
  remote accepts it; a debate keeps running while offline.
- Provenance lanes (`host-observed` / `peer-claimed` / `unattributed`) are kept,
  decided by the local database rather than by anything a peer can write.
- A thread missing from the remote is handled generically, which also covers
  debates created under h5i. No legacy h5i code.
- **Requires system `git` (≥ 2.34).** VISTA checks for it at runtime; without
  it the lab is off and the UI says "Git is not installed" (or that the
  installed git is too old), as it does today without h5i. Bundling git is a
  later option.
- **Removed:** the h5i binary dependency, per-role h5i boxes, stints and
  revocation, `box_slug` / `box_id` / `policy_digest`, `h5i browser` web reads
  (`WebReader`), the `vote_policy` / enrollment machinery and its "votes are
  being discarded" banner, and the h5i-specific `ForumSettings` fields.
- **Not in this change:** signed post commits. Attribution in v1 is the
  identity and host id each post carries, presented as a claim; signing can be
  added later as an optional post field without a format version bump.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `agent-forum`: requirements that name h5i (box-relayed attribution,
  `h5i forum close`, `h5i browser` citations, the fake-h5i test requirement,
  CLI argument lists) are replaced with backend-neutral ones, and requirements
  are added for the git thread format, local-first posting, provenance lanes,
  vote counting, missing threads, the git prerequisite, bounded attachments,
  and save-time remote verification. `agent-forum` is itself
  an unarchived change; see Impact for sequencing.

## Impact

- **Sequencing:** `agent-forum` is not yet in `openspec/specs/`. This change's
  delta applies to it, so the two are archived together when the branch merges,
  `agent-forum` first. Agreed with Junqi.
- **Backend:** `services/h5i_forum.py` (replaced), `agents/forum/{project_forum,
  debate,grounding,roles,simulation,wiring}.py`, `agents/campaign/wiring.py`,
  `services/debate.py`, `api/debate.py`, `api/projects.py`, `api/api.py`,
  `config.py` (`ForumSettings`, `forums_dir`), `db/schemas.py` (participant
  and post columns), `scripts/migrate_columns.py`.
- **Tests:** `test_h5i_forum.py` and `test_h5i_forum_live.py` are replaced by
  real-git client tests; `test_project_forum.py`, `test_debate_*.py` move to
  a fake client.
- **UI:** `ui/lib/debates.ts`, `ui/components/DebateThread.tsx`,
  `ui/app/hypothesis-lab/page.tsx`, `ui/app/api/forum/status/route.ts`,
  `ui/app/globals.css`.
- **Docs:** `docs/h5i-forum-contract.md` (replaced by a format doc),
  `docs/hypothesis-forum-hosting.md` (rewritten for plain git),
  `docs/figures/hypothesis-lab.*` (shows a partner posting through its own h5i
  box).
- **Dependencies:** removes the external h5i binary; adds a runtime dependency
  on system git ≥ 2.34 for the lab only.
- **Out of scope:** commit signing and verified attribution, Windows (its own
  MR later), whether a desktop app launched from Finder or a menu inherits
  `SSH_AUTH_SOCK`, bundling git, a VISTA-managed forge token, a standalone
  posting CLI, web grounding, migrating h5i threads.
