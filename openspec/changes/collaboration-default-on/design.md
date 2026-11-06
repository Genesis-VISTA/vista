## Context

See proposal.md for why. Current state:

- **Address.** `Settings.collaboration_server_url` (`backend/src/vista_backend/config.py`) is
  `None` by default. `services/collaboration.py::server_url` prefers the researcher's
  `LocalSettings.collaboration_server_url`, then the configuration. A blank env value parses to
  `None` (`utils/types.py`).
- **Status.** `status()` always calls `/version` then `/me` when an address is set. On a `401` it
  sets `sign_in_required` and uses `str(error)`, which is the gateway's reason phrase
  ("Unauthorized") plus `sign_in_message(url)`. On a `403`, `CollaborationForbidden` lands in the
  generic `CollaborationError` branch, so the UI's `collaborationLabel` says "Unreachable".
- **UI.**
  - `ui/lib/collaboration.ts`: `useCollaboration()` gives `available`/`reason`. `useTeams().shown`
    is `available && teams.length > 0`, and it gates the TeamSwitcher, TeamBadge and Copy to
    team…
  - The Import buttons on the Projects, Skills and Knowledge Bases pages are always drawn and
    disabled with `collab.reason`.
  - Publish shows for team items. The sharing lines (`lib/project-sharing.ts`,
    `lib/skill-sharing.ts`, `lib/kb-sharing.ts`) always show.
  - The Skill Hub entry is always in `NavRail`.
- **Status traffic.** The UI's status store fetches on first subscriber and when a new one
  arrives after 30 s. `TeamSwitcher` in `NavRail` subscribes on every app open.
- **Dev server.** The `--collab` dev-headers server signs an install in with no token.
- **Live gateway.** Obsidian's gateway answers `/version` and `/me` with
  `401 {"error":"access_denied","message":"Authorization Required"}` and a `Location` header
  (checked 2026-11-06).

## Goals / Non-Goals

**Goals:**
- One status answer decides off / quiet / shown / available, computed in the backend.
- Off means no traffic: decided in the backend, so no client can cause a request.

**Non-Goals:**
- No change to the client's request or identity logic, team rules or the server.
- No migration beyond an additive nullable column.

## Decisions

### D1: The default lives in `config.py`

`collaboration_server_url: OptionalHttpUrl = "https://obsidian.ccs.ornl.gov/vista/api"`, plus a
module constant `DEFAULT_COLLABORATION_SERVER_URL` that `default_server` compares against (after
the same normalisation).

**Why not the package launchers:** the researcher first picked the launchers to keep dev stacks
and tests off Obsidian. With the switch the traffic concern moved, and they chose `config.py`
(grilling, 2026-11-06). One place covers every platform, Windows included.

**Tests:** backend tests already patch `settings.collaboration_server_url` to `None` in every
collaboration test file. `conftest.py` gains the same patch session-wide, so an unpatched test
can't reach the network or stamp `team_server` with Obsidian.

**Dev stacks:** a dev `./launch.sh logs` stack will check Obsidian. Developers opt out with
`VISTA_BACKEND_COLLABORATION_SERVER_URL=` in `.env` or the switch, and `--collab` overrides it as
before.

### D2: `collaboration_enabled: bool | None` on `LocalSettings`

The column is nullable with no server default, so `db/schema_check.py` accepts it, and `None`
reads as on. It is exposed as `collaboration_enabled: bool` (`None` → `True`) on the public
settings, and it is writable through the existing settings update.

**Why nullable rather than NOT NULL default true:** it is equivalent in behaviour, and it leaves
"never chosen" visible should the tour or a later change want it.

### D3: The backend decides off and shown

- **Off.** `status()` returns early when off: `enabled=False`, `shown=False` and the off reason,
  without building a client. `client_for` and `required_url` raise
  `CollaborationUnavailable(OFF_REASON)` when off, so every publish, import, teams or users route
  refuses without contacting the server.
- **Shown.** `shown = bool(local.collaboration_token) or status.signed_in`.
- **Refused.** `refused` is set from `CollaborationForbidden`.
- **Default server.** `default_server` compares the address in effect with
  `DEFAULT_COLLABORATION_SERVER_URL`.

**Why in the backend:** the UI then needs no settings read to decide visibility, and off can't
leak traffic from any client.

### D4: The UI reads `shown`

- `useCollaboration()` gains `shown` and `enabled`. `useTeams().shown` requires `status.shown`
  as well as today's `available && teams.length > 0`.
- These are hidden whenever `!collab.shown`:
  - the Import buttons and the sharing lines on the Projects, Skills and Knowledge Bases pages,
    and Publish;
  - `NavRail`'s Skill Hub entry, but only when `!collab.enabled`.
- While no status has arrived, `shown` is false. Nothing flashes in and then out for a quiet
  install.

  **Trade-off:** a shown install's controls appear a moment after page load. This is accepted,
  because they were disabled until then anyway.
- The Skill Hub page has three states: off (collaboration is off + Open Settings), quiet (the
  sign-in prompt), and today's list.

### D5: Messages

- **Not signed in.** `sign_in_message(url)` alone. `_detail`'s reason phrase is no longer
  prefixed for a `401`. The text is: "Sign in to <url> to publish and import: get a token from
  its sign-in page and paste it under Settings → Collaboration."
  - **Deviation:** the storyboard's frame B ends "paste it below". The status reason is one
    string, also shown on disabled controls elsewhere, so it names the place in both.
- **Refused (403).** "<url> refused this sign-in. Signing in again won't help: ask the
  administrators of <url> for access.", or "…reach out to the VISTA team for access." on the
  default server. The server's own `detail` is dropped; the gateway's is not useful.
- **UI copy.** The hints, switch label, Skill Hub prompt and "No access yet? Reach out to the
  VISTA team." are verbatim from the storyboard (Parts 2–3).
- **Labels.** `collaborationLabel` adds "Off" and "No access".

## Risks / Trade-offs

- **[Every install now contacts Obsidian on app open]**: two GETs with the install-id header. →
  Visible and stoppable with the switch; the reworded hint no longer claims nothing leaves the
  computer.
- **[A dev stack without `--collab` checks Obsidian]** → The status is non-blocking with 5 s
  timeouts. It is documented in AGENTS.md with the `.env` opt-out.
- **[Turning off hides items that are in a team]** Their badges and Publish buttons disappear
  while off. → That is the meaning of off. They come back when it is turned on, and nothing is
  changed or deleted.
- **[`shown` false until the first answer]** A brief absence of controls on load for signed-in
  installs. → Acceptable (D4).

## Migration Plan

- **Upgrades.** The column is nullable, and `None` reads as on. Existing installs keep working,
  and those already signed in stay shown (they have a token saved).
- **Rollback.** Reverting leaves an unused nullable column, which is fine under the additive
  rule.
- **Archive order.** `collaboration-server` must be archived before this change, since
  `collaboration-client` exists only as its delta.
