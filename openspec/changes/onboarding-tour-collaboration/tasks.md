Implement after `collaboration-default-on` (this branch is stacked on it).

## 1. Step model and copy

- [ ] 1.1 Add the `collaboration` step to `ui/lib/onboarding.ts` (design.md D1): its sub-steps, `collaboration:visit` in `ACTION_SUB_STEPS`, `STEP_PAGES`, `TourInput.collaboration.enabled`, and the skip while off. Verify in `ui/tests/onboarding.test.ts`:
  - order hpc → collaboration → first-question;
  - visit done on `/skill-hub`;
  - skipped when off;
  - turning it off mid-step moves to `first-question:send`;
  - unknown counts as enabled.
- [ ] 1.2 Add the eight coachmarks and the `CopyContext` variants to `ui/lib/onboarding-copy.ts`, verbatim from `storyboard.html` Part 1, and change the `welcome:intro` and `first-question:finish` copy (D5). Verify that the existing every-sub-step-has-copy test passes, and add cases for the default-server sentence and the signed-in variant (with Manage teams…).

## 2. Examples

- [ ] 2.1 Add `EXAMPLE_TEAMS`, `EXAMPLE_HUB_SKILLS`, `EXAMPLE_LOCAL_SKILLS` and `useTourExamples()` to `ui/lib/onboarding-examples.ts` (D3). Verify with unit tests on their shapes and the hook's step condition.
- [ ] 2.2 Render the examples through `TeamFilter`/`TeamBadge`, `app/skill-hub/page.tsx` and `app/skills/page.tsx`, with no-op handlers for example items. Verify with component tests in each page's test file, using a quiet status:
  - the examples, the filter's example teams and the badges show during the step and are gone after it;
  - clicking Import, Publish… or Publish changes on an example, or choosing an example team in the filter, calls `fetch` zero times;
  - Publish… on an example does not render `PublishToTeamModal`.

## 3. Driver

- [ ] 3.1 In `ui/components/TourProvider.tsx`:
  - add the `stepKey` effects (`/skills`, `openSettings("collaboration")`, close on leaving);
  - add `local` to `CENTRED` and `sign-in`/`off` to `USABLE_WHILE_EXPLAINED`;
  - add the `data-tour` anchors (D2, D4).

  Verify in `ui/tests/TourProvider.test.tsx` that each position anchors to its element, Settings opens at `sign-in` and closes on leaving or exiting, and turning the switch off at `off` lands on `first-question:send`.

## 4. Checks

- [ ] 4.1 Run `./scripts/ci-local.sh ui lint` and `./scripts/ci-local.sh ui test`, and confirm both pass. (These run `next build` into `ui/.next`; stop any `./launch.sh` stack in this checkout first.)
- [ ] 4.2 Manual, not in PR CI: on a fresh-data dev stack, walk all eight coachmarks in a real browser, signed out on the default server and then signed in with `--collab`. Compare each with `storyboard.html` Part 1, and confirm that the step is skipped when collaboration is off. Record the result here.
- [ ] 4.3 Run `openspec validate onboarding-tour-collaboration` and confirm it passes.
- [ ] 4.4 Archive `onboarding-tour-collaboration` (`/opsx:archive`), after `collaboration-default-on`, which this branch already archives. Confirm `openspec validate --all` passes afterwards.
