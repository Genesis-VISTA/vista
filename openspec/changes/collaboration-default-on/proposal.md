## Why

Collaboration is off on every new install until a researcher finds Settings › Collaboration and
types the server's address. The only hint is a placeholder, so the address is lost as soon as
typing starts. Once an address is set, every not-signed-in researcher sees disabled Import
buttons, "Not published" lines and a sign-in box across the app, and there is no way to turn any
of it off. The message on all of them is a raw "Unauthorized Get a token from…". Collaboration
is a big feature: it should be on by default, quiet until it can work, and possible to switch
off.

## What Changes

- **Default address.** The backend's configured collaboration server defaults to
  `https://obsidian.ccs.ornl.gov/vista/api`. The researcher's address in Settings still wins,
  and a blank `VISTA_BACKEND_COLLABORATION_SERVER_URL` still means none.
- **A Collaboration switch** in Settings, on by default. An install that never touched it is on.
  - **When off,** VISTA sends nothing to any collaboration server. It hides the Skill Hub entry,
    the team menu, team badges, Publish, Import, Copy to team… and the sharing lines. Settings
    shows only the switch and its hint.
- **Quiet until signed in.** With the switch on, nothing about collaboration shows outside
  Settings and the Skill Hub until a token is saved, or the server says this install is signed
  in.
  - After that, everything shows and stays shown. An expired token, a refusal or an offline
    server leaves controls disabled, with the reason.
  - Before that, the Skill Hub shows "Sign in to see what others share", with Open Settings.
- **Clearer messages.**
  - The not-signed-in reason drops the raw "Unauthorized".
  - A signed-in install the server refuses (403) is labelled "No access", not "Unreachable".
  - On the default server, the sign-in places say "No access yet? Reach out to the VISTA team."
- **The Settings hint is reworded** to "Your projects, skills and knowledge bases stay on this
  computer until you publish them", since status checks now reach the server.

Approved wording and screens: storyboard Parts 2–3,
<https://claude.ai/artifact/RR2FxaXaoPvULbMxY6DNDG>.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `collaboration-client`:
  - the address gets a default;
  - the status reports the switch, quietness and refusal;
  - controls are hidden when off or quiet;
  - new requirements for the switch and the quiet state.

`collaboration-client` is still only a delta in `openspec/changes/collaboration-server/`. Archive
`collaboration-server` before archiving this change.

Related: `onboarding-tour-collaboration`, which teaches this behaviour and depends on this
change. The team rules are in the `collaboration-server` access-control work.

## Non-goals

- No request-access link or mailbox. The prompt only says to reach out to the VISTA team.
- No change to the collaboration server, its gateway or its allowlist.
- No change to `./launch.sh --collab` or the package launchers. The default lives in
  `config.py`, and the environment variable still overrides it.
- No VISTAGuard work.

## Impact

- **Backend:**
  - `config.py` (the default)
  - `db/schemas.py` (nullable `collaboration_enabled`, additive)
  - `services/collaboration.py` (off and quiet in the status; messages)
  - the collaboration routes, which refuse while off
- **UI:**
  - `ui/lib/collaboration.ts`
  - `NavRail`, `TeamSwitcher`
  - the Projects, Skills and Knowledge Bases pages
  - `skill-hub/page.tsx`
  - `UserSettingsModal.tsx`
  - `e2e-hermetic/fixtures.ts`
- **Docs:** `AGENTS.md`'s collaboration paragraph, `docs/collaboration-server.md`, and the
  collaboration sections of `docs/skill-onboarding.md` and `docs/project-onboarding.md`.
