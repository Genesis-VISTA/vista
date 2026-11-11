## Why

Collaboration isn't intuitive. Researchers don't know:
- that nothing is shared until they publish or import;
- what Private, a team and Public mean;
- where to sign in, or that they can turn it off.

The first-boot tour covers every other area of VISTA and says nothing about it. Most people
taking the tour are not signed in, and many have no access yet, so the real team UI is hidden
from them: the tour has to show it with examples.

## What Changes

- **A new `collaboration` step** between `hpc` and `first-question`. Its eight coachmarks:
  1. *visit*: open Skill Hub. The only action-type coachmark; the rest pass with Next.
  2. *overview*: what the Skill Hub is.
  3. *local*: "Nothing is shared on its own".
  4. *teams*: Private, a team, or Public, on the Skill Hub's team filter and badges.
  5. *hub-item*: a shared skill and Import.
  6. *share*: on Skills, Publish… for something private (its team is picked the first time,
     and it stays there), and Publish changes for a team item.
  7. *sign-in*: in Settings › Collaboration, with "No access yet? Reach out to the VISTA team."
     on the default server, or, signed in, where Manage teams… is.
  8. *off*: "Working alone?", pointing at the switch.
- **Tour-only examples in the real components.**
  - The Skill Hub's team filter lists Public and "Example team", and the cards show team
    badges, for everyone.
  - The Skill Hub shows two example skills, and the Skills page two more.
  - All are marked "Example", for every researcher, signed in or not, and only while the step
    runs. Their Import, Publish… and Publish changes do nothing, and no example ever reaches
    the backend.
- **The step is skipped while collaboration is off**, and turning it off at coachmark 8 ends the
  step.
- **The welcome** says the tour takes about nine minutes. **The finish** adds "and learned how
  sharing works".
- Installs that already finished the tour are not shown it again; Replay tutorial includes it.

The wording and screens are the approved storyboard's (Part 1), committed beside this proposal
as `storyboard.html` (version 6, redrawn after main's collaboration simplification, also at
<https://claude.ai/artifact/RR2FxaXaoPvULbMxY6DNDG>).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `onboarding-tour`:
  - the step order gains `collaboration`, and the welcome says nine minutes;
  - the Skill Hub joins the screens that open with an overview;
  - new requirements for the collaboration step and its examples.

**Depends on `collaboration-default-on`.** The step teaches its switch, its quiet state and its
wording, and is skipped by its `enabled`. This branch is stacked on that one.

## Non-goals

- No "what's new" nudge for installs that finished an older tour.
- No sign-in or switch change required to finish the step.
- No request-access link.
- No VISTAGuard work.

## Impact

- **UI:**
  - `ui/lib/onboarding.ts` (steps, sub-steps, the skip, actions)
  - `ui/lib/onboarding-copy.ts`
  - `ui/lib/onboarding-examples.ts` (example teams and skills)
  - `ui/components/TourProvider.tsx` (navigation, Settings, anchors)
  - `TeamFilter.tsx` and `TeamBadge.tsx` (example teams)
  - `app/skill-hub/page.tsx`
  - `app/skills/page.tsx`
  - `UserSettingsModal.tsx` (anchors)
- **Tests:** `ui/tests/onboarding.test.ts`, `ui/tests/TourProvider.test.tsx`
