## Context

See proposal.md for why. The tour is a pure step model plus a driver:

- **Step model.** `ui/lib/onboarding.ts` holds `TOUR_STEPS` and `TOUR_SUB_STEPS`. `isSubStepDone`
  is a pure switch over `TourInput`. `ACTION_SUB_STEPS` lists the coachmarks with no Next.
  `STEP_PAGES` gives a `visit` coachmark its page. `ANCHOR_ALIASES` maps a position to the
  `data-tour` key it anchors to.
- **Driver.** `ui/components/TourProvider.tsx` anchors driver.js to `[data-tour="<key>"]`.
  - An overview anchors to `.page-topbar`, undimmed.
  - A missing anchor falls back to a centred popover after 3 s.
  - Next-type coachmarks are look-only, except for keys in `USABLE_WHILE_EXPLAINED`.
  - A `stepKey` effect navigates (`router.push`) and opens or closes Settings
    (`openSettings(section)`), as `model:provider` does.
- **Copy and examples.** `ui/lib/onboarding-copy.ts` holds the copy, keyed `"step:sub"`, with a
  `CopyContext` for variants. A vitest checks that every sub-step has copy.
  `ui/lib/onboarding-examples.ts` holds the files step's examples, which `app/files/page.tsx`
  merges into its list only while `tour.position?.step === "files"`.
- **Collaboration gates** (after `collaboration-default-on`). `useCollaboration()` gives
  `enabled`, `shown`, `available`, `default_server`, `signed_in` and `url`. The TeamSwitcher and
  TeamBadge render nothing unless `useTeams().shown`. The Skill Hub has off, quiet and listed
  states.

## Goals / Non-Goals

**Goals:**
- The step model stays pure and unit-tested: skipping, order and the action set are decided in
  `onboarding.ts`.
- Examples go through the real components, so the tour shows what the researcher will use and
  follows UI changes.
- An example can never cause a request.

**Non-Goals:**
- No new tour mechanism. Everything reuses the existing driver, anchors, events, navigation and
  example patterns.

## Decisions

### D1: The step model

- **Steps.** Add `"collaboration"` to `TOUR_STEPS` between `hpc` and `first-question`, with
  sub-steps `visit, overview, local, teams, hub-item, share, sign-in, off`.
- **Visit.** `collaboration:visit` joins `ACTION_SUB_STEPS`. `STEP_PAGES.collaboration` is
  `/skill-hub`, so it is done by pathname like the other visits.
- **Input.** `TourInput` gains `collaboration: { enabled: boolean }`, wired in `TourProvider` from
  `useCollaboration()`.
- **Skip.** `nextTourStep` treats the step as skipped while `!enabled`, the same way it treats a
  step the researcher skipped. This one rule covers both "skipped when off" and "turning it off
  ends the step".

  **Until the first status arrives**, `enabled` is unknown. The model treats unknown as enabled,
  and the step is still eight steps away at boot, so it is known long before it is reached.
- **Look-only.** `local` is centred (added to `CENTRED`). `sign-in` and `off` join
  `USABLE_WHILE_EXPLAINED`.

### D2: Navigation in `TourProvider`'s `stepKey` effect

| Position | Effect |
|---|---|
| `collaboration:teams` | opens the team menu, through a tour flag the switcher reads, and closes it on leaving |
| `collaboration:share` | `router.push("/skills")` |
| `collaboration:sign-in` | `openSettings("collaboration")` |
| leaving the step (any way) | `closeSettings()` |

`first-question:send` already navigates to chat.

### D3: Examples through the real components

`onboarding-examples.ts` gains the following, each with `example: true`:
- `EXAMPLE_TEAMS`: Private, Public and Example team;
- `EXAMPLE_HUB_SKILLS`: `plot-results` (Public) and `batch-job-submit` (Example team), shaped as
  the Skill Hub's remote skills;
- `EXAMPLE_LOCAL_SKILLS`: `csv-cleanup` (Private) and `run-summary` (Example team, changed
  here), shaped as the Skills page's rows.

Each component checks one hook, `useTourExamples()`, which is true while
`tour.position?.step === "collaboration"`:
- **TeamSwitcher** renders the example list in place of `useTeams()`, bypassing `shown`.
  Choosing an item is a no-op, so no `collaboration_team_id` is written.
- **TeamBadge** renders for any item carrying an example team.
- **Skill Hub page** renders the example cards in place of its off, quiet or listed content.
- **Skills page** merges the example rows into its list, as the files step does. A real skill
  with the same name wins.

**Safety:**
- Every handler a collaboration control calls (import, copy to team, publish, switch team)
  returns early for an `example` item before any fetch. A test asserts that `fetch` is never
  called.
- Examples are never written to a store; they exist only in render.

**Why bypass the gates rather than fake a status:** a fake status would flow into every
`useCollaboration()` reader, including Publish on real items and Settings, and could make a real
control look enabled. Scoping the examples to the components the step shows keeps real controls
on real state.

### D4: Anchors

New `data-tour` keys:

| Key | Element |
|---|---|
| `collaboration:visit` | the Skill Hub rail entry |
| `collaboration:teams` | the switcher with its menu |
| `collaboration:hub-item` | the first example hub card |
| `collaboration:share` | the example private skill card |
| `collaboration:sign-in` | a wrapper around Settings' status line and Token field |
| `collaboration:off` | the switch |

The sign-in wrapper always renders while on, so a server with no Token field (dev-headers) still
has an anchor.

### D5: Copy

All coachmark text is verbatim from `storyboard.html` Part 1. `CopyContext` gains:
- `collabSignedIn` and `collabUrl`, for the signed-in variant of `sign-in`;
- `collabDefaultServer`, which adds the VISTA team sentence.

`welcome:intro` says nine minutes, and `first-question:finish` gains "and learned how sharing
works".

## Risks / Trade-offs

- **[A signed-in researcher sees example teams in place of their own for one coachmark]** This
  could confuse. → The items are marked Example, and the real list returns on leaving the step.
  It was chosen for one path and no risk of a real publish (grilling, 2026-11-06).
- **[Gate bypasses drift]** A later component change could drop the bypass. → Tests render each
  component in the step with a quiet status and assert the examples show.
- **[The team menu opened by the tour stays open]** → It is closed in the same `stepKey` effect
  on any position change, including exit.

## Migration Plan

UI only. Tour state stores a step id, and an install mid-tour on `hpc` goes on to `collaboration`
after upgrading. Finished installs are untouched.
